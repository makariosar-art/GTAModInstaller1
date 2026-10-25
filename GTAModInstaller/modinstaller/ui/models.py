from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..analyzer import SourcePlan
from ..staging import PreparedSource
from ..utils import files_word, plural_ru


@dataclass
class SourceState:
    path: Path | None
    title: str
    meta: dict = field(default_factory=dict)
    origin: str = "file"
    key: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "queued"
    message: str = "В очереди"
    error: str = ""
    hint: str = ""
    source: PreparedSource | None = None
    plan: SourcePlan | None = None
    auto_prompt: bool = False
    image: str = ""
    page_url: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.plan is not None

    @property
    def installable(self) -> bool:
        return bool(self.plan and self.plan.installable and self.plan.enabled_items)

    @property
    def subtitle(self) -> str:
        if self.status == "ready" and self.plan is not None:
            item_count = len(self.plan.enabled_items)
            manual = len(self.plan.manual_items)
            conflicts = self.plan.conflict_count
            bits = [f"{item_count} {files_word(item_count)}"]
            if conflicts:
                bits.append(f"{conflicts} {plural_ru(conflicts, 'замена', 'замены', 'замен')}")
            if manual:
                bits.append(f"{manual} вручную")
            if self.plan.deps:
                bits.append(f"{len(self.plan.deps)} завис.")
            return " · ".join(bits)
        if self.status == "error":
            return self.error or "Ошибка анализа"
        return self.message

    def cleanup(self) -> None:
        if self.source is not None:
            try:
                self.source.cleanup()
            except Exception:
                pass
            self.source = None
        self.plan = None
