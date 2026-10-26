"""通过本地评测模式的 /add、/search 采集原 CaseOutcome；不修改 QA 逻辑。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import timezone
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import httpx

from .artifacts import write_json
from .harness import EvalHarness
from .types import CaseOutcome, Dataset, MemorySeed, QueryCase

logger = logging.getLogger(__name__)


class HttpEvalHarness:
    """只发送历史与查询；gold、相关性标注和 Judge 均留在评测端。"""

    def __init__(self, base_url: str, artifact_dir: str | Path | None = None) -> None:
        token = os.environ.get("LME_MEMORY_API_KEY", "")
        if not token:
            raise ValueError("LME_MEMORY_API_KEY is required for HTTP evaluation")
        self._client = httpx.Client(
            base_url=base_url,
            timeout=1800,
            trust_env=False,
            headers={"Authorization": "Bearer " + token},
        )
        self._key2ids: dict[str, list[str]] = {}
        self._artifact_dir = Path(artifact_dir) if artifact_dir else None
        self._audits: list[dict] = []

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _user_id(scope) -> str:
        return hashlib.sha256(json.dumps(EvalHarness._scope_key(scope)).encode()).hexdigest()

    def ingest(self, seeds: list[MemorySeed]) -> None:
        for index, seed in enumerate(seeds, start=1):
            message = {"role": "user", "content": seed.content}
            if seed.occurred_at is not None:
                stamp = seed.occurred_at
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                message["timestamp"] = int(stamp.timestamp() * 1000)
            payload = {
                "request_id": hashlib.sha256(seed.key.encode()).hexdigest(),
                "user_id": self._user_id(seed.scope),
                "session_id": seed.metadata.get("session") or "evaluation-session",
                "messages": [message],
                "evaluation_seed": {
                    "metadata": dict(seed.metadata),
                    "tags": list(seed.tags),
                    "occurred_at": seed.occurred_at.isoformat() if seed.occurred_at else None,
                },
            }
            logger.info("HTTP Add %s/%s: %s", index, len(seeds), seed.key)
            started = perf_counter()
            response = self._client.post("/add", json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get("success") is not True or any(
                data.get(k) != payload[k] for k in ("request_id", "user_id", "session_id")
            ):
                raise RuntimeError("Add response envelope mismatch")
            unit_ids = data.get("evaluation", {}).get("unit_ids")
            if not isinstance(unit_ids, list) or any(not isinstance(uid, str) for uid in unit_ids):
                raise RuntimeError("HTTP server must enable repository evaluation metadata")
            self._key2ids[seed.key] = unit_ids
            logger.info(
                "HTTP Add done: units=%s elapsed=%.3fs", len(unit_ids), perf_counter() - started
            )

    def run_query(self, case: QueryCase) -> CaseOutcome:
        if case.filters or case.as_of is not None:
            raise ValueError(
                "HTTP evaluation does not support filters/as_of; refusing to drop them"
            )
        started = perf_counter()
        logger.info("HTTP Search: %s top_k=%s", case.query_id, case.top_k)
        response = self._client.post(
            "/search",
            json={"query": case.text, "user_id": self._user_id(case.scope), "top_k": case.top_k},
        )
        response.raise_for_status()
        http_ms = (perf_counter() - started) * 1000
        data = response.json()
        items, observation = data["data"], data["evaluation"]
        if any("message_at" not in item or "event_at" not in item for item in items):
            raise RuntimeError("HTTP evaluation response lacks temporal metadata")
        relevant = {uid for key in case.relevant_keys for uid in self._key2ids.get(key, [])}
        source_keys = case.relevant_source_keys or {key: {key} for key in case.relevant_keys}
        outcome = CaseOutcome(
            query_id=case.query_id,
            query_text=case.text,
            ranked_unit_ids=[item["id"] for item in items],
            relevant_unit_ids=relevant,
            contents=[item["content"] for item in items],
            context_dates=[item["message_at"] or item["event_at"] for item in items],
            context_message_dates=[item["message_at"] for item in items],
            context_event_dates=[item["event_at"] for item in items],
            trajectory=[SimpleNamespace(**row) for row in observation["trajectory"]],
            memory_retrieval_e2e_wall_ms=observation["memory_retrieval_e2e_wall_ms"],
            storage_recall_wall_ms=observation["storage_recall_wall_ms"],
            expected_answer=case.expected_answer,
            metadata={
                **case.metadata,
                "memory_transport": "http",
                "http_search_wall_ms": str(http_ms),
            },
            relevant_key_unit_ids={
                source: [uid for key in keys for uid in self._key2ids.get(key, [])]
                for source, keys in source_keys.items()
            },
        )
        self._audits.append(
            {
                "query_id": case.query_id,
                "http_search_wall_ms": http_ms,
                "response": data,
                "seed_unit_ids": dict(self._key2ids),
            }
        )
        return outcome

    def evaluate(self, dataset: Dataset, concurrency: int = 1) -> list[CaseOutcome]:
        if concurrency != 1:
            raise ValueError("HTTP repository evaluation currently requires concurrency=1")
        groups: dict[str, list[MemorySeed]] = {}
        for seed in dataset.seeds():
            groups.setdefault(self._user_id(seed.scope), []).append(seed)
        outcomes, written = [], set()
        for case in dataset.queries():
            user = self._user_id(case.scope)
            if user not in written:
                self.ingest(groups.get(user, []))
                written.add(user)
            outcomes.append(self.run_query(case))
        if self._artifact_dir:
            write_json(self._artifact_dir / "http_memory_observations.json", self._audits)
        return outcomes
