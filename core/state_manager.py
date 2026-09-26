"""
考研数学智能组卷系统 - 状态管理与多用户错题本持久化服务 (Clean-Room 原创实现)
支持多用户/多科目独立隔离存储、做错次数追踪、错题本导入导出与跨设备迁移
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
import threading
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.models import QuestionItem, SubjectType, WrongQuestionRecord


_state_rlock = threading.RLock()  # 同进程读写互斥（fragment 局部 rerun / 回调 / cron 线程）

logger = logging.getLogger("pinpaper.state")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())


class StateManager:
    """错题本管理与复习进度状态追踪服务（支持多用户与多科目档案隔离）"""

    def __init__(
        self,
        username: str = "default",
        subject: SubjectType | str | None = None,
        data_file: Path | str | None = None,
        base_dir: Path | str | None = None,
    ):
        self.subject = subject.value if isinstance(subject, SubjectType) else (str(subject) if subject else "")
        
        if data_file is not None:
            self.data_file = Path(data_file).resolve()
            self.username = self.data_file.stem
            self.storage_dir = self.data_file.parent
        else:
            self.username = self._clean_username(username)
            project_root = Path(__file__).resolve().parent.parent
            self.storage_dir = (Path(base_dir) if base_dir else project_root / "user_data").resolve()
            self.storage_dir.mkdir(parents=True, exist_ok=True)

            sub_suffix = f"_{self.subject}" if self.subject else ""
            default_file = self.storage_dir / f"wrong_notebook_default{sub_suffix}.json"
            target_file = self.storage_dir / f"wrong_notebook_{self.username}{sub_suffix}.json"
            legacy_file = project_root / "user_wrong_notebook.json"

            if not target_file.exists() or target_file.stat().st_size < 10:
                if default_file.exists() and default_file.stat().st_size > 10:
                    try:
                        target_file.write_text(default_file.read_text(encoding="utf-8"), encoding="utf-8")
                    except Exception:
                        pass
                elif legacy_file.exists() and not self.subject:
                    try:
                        target_file.write_text(legacy_file.read_text(encoding="utf-8"), encoding="utf-8")
                    except Exception:
                        pass

            self.data_file = target_file

        self.wrong_questions: dict[str, WrongQuestionRecord] = {}
        self.historical_seen_ids: set[str] = set()
        self.historical_covered_chapters: set[str] = set()
        self.last_papers_qids: list[list[str]] = []  # 上次生成的试卷（每份卷一个题号列表）
        self._processed_daily: dict[str, list[str]] = {}  # 按日期持久化的"今日已处理"qid
        self._assigned_daily: dict[str, list[str]] = {}  # 按日期持久化的"今日安排"qid（每天只自动安排一次）
        self.load_state()

    @staticmethod
    def _clean_username(name: str) -> str:
        s = str(name).strip().replace(" ", "_").replace("/", "").replace("\\", "")
        return s if s else "default"

    def switch_user(self, new_username: str) -> None:
        """切换当前活跃用户档案"""
        cleaned = self._clean_username(new_username)
        if cleaned == self.username:
            return
        self.username = cleaned
        sub_suffix = f"_{self.subject}" if self.subject else ""
        self.data_file = self.storage_dir / f"wrong_notebook_{cleaned}{sub_suffix}.json"
        self.wrong_questions = {}
        self.historical_seen_ids = set()
        self.historical_covered_chapters = set()
        self.load_state()

    def get_all_profiles(self) -> list[str]:
        """获取本地已存储的所有用户档案名称"""
        profiles = set()
        for p in self.storage_dir.glob("wrong_notebook_*.json"):
            name = p.stem.replace("wrong_notebook_", "")
            # remove subject suffix if present
            for sub in ["_数学一", "_数学二", "_数学三"]:
                if name.endswith(sub):
                    name = name[:-len(sub)]
            if name:
                profiles.add(name)
        if not profiles:
            profiles.add("default")
        return sorted(profiles)

    def load_state(self) -> None:
        with _state_rlock:
            self._load_state_unlocked()

    def _load_state_unlocked(self) -> None:
        if not self.data_file.exists():
            return
        try:
            payload = json.loads(self.data_file.read_text(encoding="utf-8"))
            records = payload.get("wrong_questions", {})
            for qid, data in records.items():
                self.wrong_questions[qid] = WrongQuestionRecord(
                    question_id=qid,
                    added_at=data.get("added_at", ""),
                    user_note=data.get("user_note", ""),
                    error_tag=data.get("error_tag", "概念模糊"),
                    wrong_count=int(data.get("wrong_count", 1)),
                    is_active_in_pool=bool(data.get("is_active_in_pool", True)),
                    subject=data.get("subject", self.subject),
                    last_reviewed_at=data.get("last_reviewed_at", ""),
                    next_review_at=data.get("next_review_at", ""),
                    review_stage=int(data.get("review_stage", 0)),
                )
            self.historical_seen_ids = set(payload.get("seen_question_ids", []))
            # 迁移:凡录过错题的题一律视为已做过(标错=纸质书做过),拼卷不再当新题推
            for _qid in self.wrong_questions:
                self.historical_seen_ids.add(_qid)
            self.historical_covered_chapters = set(payload.get("covered_chapters", []))
            self.last_papers_qids = payload.get("last_papers_qids", []) or []
            self._processed_daily = payload.get("processed_daily", {}) or {}
            self._assigned_daily = payload.get("assigned_daily", {}) or {}
        except Exception:
            pass

    def save_state(self) -> None:
        """原子写 + 线程锁：临时文件 + fsync + os.replace。
        写失败不再静默——记录日志（pythonw 后台 → logs/app.log 可查）。"""
        with _state_rlock:
            try:
                payload = {
                    "username": self.username,
                    "subject": self.subject,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "wrong_questions": {
                        qid: rec.to_dict() for qid, rec in self.wrong_questions.items()
                    },
                    "seen_question_ids": list(self.historical_seen_ids),
                    "covered_chapters": list(self.historical_covered_chapters),
                    "last_papers_qids": self.last_papers_qids,
                    "processed_daily": self._processed_daily,
                    "assigned_daily": self._assigned_daily,
                }
                fd, tmp = tempfile.mkstemp(dir=str(self.data_file.parent), prefix=".state_", suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, self.data_file)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except Exception:
                logger.exception("save_state 失败: %s", self.data_file)

    def toggle_wrong_question(
        self,
        question_id: str,
        note: str = "",
        error_tag: str = "概念模糊",
    ) -> bool:
        if question_id in self.wrong_questions:
            del self.wrong_questions[question_id]
            is_added = False
        else:
            self.wrong_questions[question_id] = WrongQuestionRecord(
                question_id=question_id,
                user_note=note,
                error_tag=error_tag,
                wrong_count=1,
                is_active_in_pool=True,
                subject=self.subject,
            )
            self.historical_seen_ids.add(question_id)  # 标错=已做过,拼卷不再当新题推
            is_added = True
        self.save_state()
        return is_added

    def remove_wrong_question(self, question_id: str) -> bool:
        if question_id in self.wrong_questions:
            del self.wrong_questions[question_id]
            self.save_state()
            return True
        return False

    def set_wrong_count(self, question_id: str, count: int) -> None:
        if count <= 0:
            if question_id in self.wrong_questions:
                del self.wrong_questions[question_id]
        else:
            if question_id in self.wrong_questions:
                self.wrong_questions[question_id].wrong_count = count
                self.wrong_questions[question_id].is_active_in_pool = True
            else:
                self.wrong_questions[question_id] = WrongQuestionRecord(
                    question_id=question_id,
                    wrong_count=count,
                    is_active_in_pool=True,
                    subject=self.subject,
                )
                self.historical_seen_ids.add(question_id)  # 标错=已做过
        self.save_state()

    def increment_wrong_count(self, question_id: str, delta: int = 1) -> int:
        if question_id in self.wrong_questions:
            rec = self.wrong_questions[question_id]
            rec.is_active_in_pool = True
            rec.wrong_count = max(1, rec.wrong_count + delta)
            self.save_state()
            return rec.wrong_count
        else:
            self.wrong_questions[question_id] = WrongQuestionRecord(
                question_id=question_id,
                wrong_count=max(1, delta),
                is_active_in_pool=True,
                subject=self.subject,
            )
            self.historical_seen_ids.add(question_id)  # 标错=已做过
            self.save_state()
            return self.wrong_questions[question_id].wrong_count

    def mark_solved_correctly(self, question_id: str) -> None:
        if question_id in self.wrong_questions:
            self.wrong_questions[question_id].is_active_in_pool = False
            self.save_state()
        else:
            self.wrong_questions[question_id] = WrongQuestionRecord(
                question_id=question_id,
                wrong_count=0,
                is_active_in_pool=False,
                subject=self.subject,
            )
            self.save_state()

    def reactivate_to_pool(self, question_id: str) -> None:
        if question_id in self.wrong_questions:
            self.wrong_questions[question_id].is_active_in_pool = True
            if self.wrong_questions[question_id].wrong_count <= 0:
                self.wrong_questions[question_id].wrong_count = 1
        else:
            self.wrong_questions[question_id] = WrongQuestionRecord(
                question_id=question_id,
                wrong_count=1,
                is_active_in_pool=True,
                subject=self.subject,
            )
        self.save_state()

    def is_in_active_pool(self, question_id: str) -> bool:
        rec = self.wrong_questions.get(question_id)
        return bool(rec and rec.is_active_in_pool and rec.wrong_count > 0)

    def is_temporarily_mastered(self, question_id: str) -> bool:
        rec = self.wrong_questions.get(question_id)
        return bool(rec and (not rec.is_active_in_pool) and rec.wrong_count > 0)

    def is_wrong_marked(self, question_id: str) -> bool:
        rec = self.wrong_questions.get(question_id)
        return bool(rec and rec.wrong_count > 0)

    def get_wrong_count(self, question_id: str) -> int:
        rec = self.wrong_questions.get(question_id)
        return rec.wrong_count if rec else 0

    def get_active_wrong_question_ids(self) -> set[str]:
        return {qid for qid, rec in self.wrong_questions.items() if rec.is_active_in_pool and rec.wrong_count > 0}

    def get_all_wrong_question_ids(self) -> set[str]:
        return set(self.wrong_questions.keys())

    def get_wrong_question_ids(self) -> set[str]:
        return self.get_active_wrong_question_ids()

    def get_active_wrong_pool(self) -> set[str]:
        return self.get_active_wrong_question_ids()

    def batch_mark_wrong(self, question_ids: list[str], error_tag: str = "概念模糊") -> int:
        count = 0
        for qid in question_ids:
            if qid not in self.wrong_questions:
                self.wrong_questions[qid] = WrongQuestionRecord(
                    question_id=qid,
                    error_tag=error_tag,
                    wrong_count=1,
                    is_active_in_pool=True,
                    subject=self.subject,
                )
                count += 1
            else:
                self.wrong_questions[qid].is_active_in_pool = True
                self.wrong_questions[qid].wrong_count += 1
                count += 1
        if count > 0:
            self.save_state()
        return count

    def batch_unmark_wrong(self, question_ids: list[str]) -> int:
        count = 0
        for qid in question_ids:
            if qid in self.wrong_questions:
                del self.wrong_questions[qid]
                count += 1
        if count > 0:
            self.save_state()
        return count

    def clear_all_wrong(self) -> None:
        self.wrong_questions.clear()
        self.save_state()

    def record_paper_generation(self, questions: list[QuestionItem]) -> None:
        for q in questions:
            self.historical_seen_ids.add(q.id)
            self.historical_covered_chapters.add(q.chapter)
        self.save_state()

    def set_last_papers(self, papers_qids: list[list[str]]) -> None:
        """记录上次生成的试卷题号（每份卷一个列表），落本地文件供重开恢复。"""
        self.last_papers_qids = papers_qids
        self.save_state()

    def reset_coverage_cycle(self) -> None:
        self.historical_covered_chapters.clear()
        self.save_state()

    def export_wrong_questions_json(self) -> str:
        payload = {
            "version": "1.0",
            "username": self.username,
            "subject": self.subject,
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_count": len(self.wrong_questions),
            "records": [rec.to_dict() for rec in self.wrong_questions.values()],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def import_wrong_questions_json(
        self,
        raw_json_str: str,
        valid_ids: set[str] | None = None,
        merge: bool = True,
    ) -> tuple[int, int]:
        """从 JSON 备份恢复错题。返回 (导入条数, 因题库变动被跳过的条数)。

        - valid_ids 提供时，仅导入当前题库仍存在的题号，其余跳过（跨版本安全）。
        - merge=True 合并进现有错题本；merge=False 先清空再导入。
        """
        data = json.loads(raw_json_str)
        records = data.get("records", [])
        if not merge:
            self.wrong_questions = {}
        imported = 0
        skipped = 0
        for item in records:
            qid = item.get("question_id")
            if not qid:
                continue
            if valid_ids is not None and qid not in valid_ids:
                skipped += 1
                continue
            _rec = WrongQuestionRecord(
                question_id=qid,
                added_at=item.get("added_at", ""),
                user_note=item.get("user_note", ""),
                error_tag=item.get("error_tag", "概念模糊"),
                wrong_count=int(item.get("wrong_count", 1)),
                is_active_in_pool=bool(item.get("is_active_in_pool", True)),
                subject=item.get("subject", self.subject),
            )
            # 导入文件若缺少进度字段,保留本地已有进度,避免导入即清空艾宾浩斯曲线(DS 审查高危项);
            # 笔记/标签:备份文件非空则优先采用备份值(恢复用户内容),本地值仅兜底
            self._keep_local_meta(_rec, self.wrong_questions.get(qid), prefer_local_note=False)
            self.wrong_questions[qid] = _rec
            imported += 1
        self.save_state()
        return (imported, skipped)

    # =====================================================================
    # URL 位图编码：把错题状态压成可放进页面 URL 的紧凑串，实现无后端跨设备恢复
    # ---------------------------------------------------------------------
    # 每道题用 2 bit 记录状态（顺序 = 题库 canonical 顺序）：
    #   0 未标记 | 1 待练(错1次) | 2 待练·顽固(错≥2) | 3 历史错题(已归档)
    # 打包后 zlib 压缩(大量连续 0 压缩率极高) + urlsafe base64。
    # 串首带题库签名 sig，题库结构变化时签名不匹配即拒绝恢复，避免错位。
    # 注意：精确做错次数只保留"1 / ≥2"两档；自由备注/时间戳不进 URL。
    # =====================================================================
    URL_CODE_PREFIX = "w1"

    @staticmethod
    def bank_signature(ordered_ids: list[str]) -> str:
        """题库 canonical ID 列表的短签名，用于校验 URL 位图与当前题库是否匹配"""
        joined = "\n".join(ordered_ids)
        return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def _keep_local_meta(record: "WrongQuestionRecord", local: "WrongQuestionRecord | None",
                         prefer_local_note: bool = True) -> None:
        """统一保留本地已有记录的关键元数据(单一入口,DS 审查要求)。

        所有会"重建/覆盖"错题记录的路径(URL 恢复、JSON 导入等)都必须调用本方法:
        - 进度字段(added_at/review_stage/last_reviewed_at/next_review_at):一律保留本地,
          避免重建时 added_at 被刷成当前时间、艾宾浩斯曲线被清空(本次 bug 根因)。
        - 用户内容字段(user_note/error_tag):prefer_local_note=True(URL 恢复等)用本地值;
          prefer_local_note=False(JSON 备份导入等)时,备份文件非空则优先采用备份值,
          本地值仅作兜底——保证"从备份恢复"能还原用户笔记,不会被本地空值覆盖(DS 复审)。
        """
        if local is None:
            return
        record.added_at = local.added_at
        record.review_stage = local.review_stage
        record.last_reviewed_at = local.last_reviewed_at
        record.next_review_at = local.next_review_at
        if prefer_local_note or not record.user_note:
            record.user_note = local.user_note
        if prefer_local_note or not record.error_tag or record.error_tag == "概念模糊":
            record.error_tag = local.error_tag

    def _state_code_for(self, qid: str) -> int:
        rec = self.wrong_questions.get(qid)
        if not rec or rec.wrong_count <= 0:
            return 0
        if rec.is_active_in_pool:
            return 2 if rec.wrong_count >= 2 else 1
        return 3  # 已归档历史错题

    def to_url_code(self, ordered_ids: list[str]) -> str:
        """将当前科目错题状态编码为可放进 URL 的紧凑串"""
        n = len(ordered_ids)
        packed = bytearray((n + 3) // 4)
        for i, qid in enumerate(ordered_ids):
            code = self._state_code_for(qid)
            if code:
                packed[i >> 2] |= code << ((i & 3) * 2)
        compressed = zlib.compress(bytes(packed), 9)
        b64 = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
        return f"{self.URL_CODE_PREFIX}~{self.bank_signature(ordered_ids)}~{b64}"

    def apply_url_code(self, code: str, ordered_ids: list[str], merge: bool = False) -> tuple[int, str]:
        """从 URL 串恢复错题状态。返回 (恢复的错题数, 状态说明)。

        - 前缀或格式不符 -> (0, 'invalid')
        - 题库签名不匹配 -> (0, 'stale')：题库已变，老链接失效，拒绝套用以免错位
        - 空错题(全 0)   -> (0, 'empty')：不覆盖本地已有数据
        - 成功           -> (个数, 'ok')

        merge=False:整体替换 wrong_questions(单书/首个书恢复,保持原语义)。
        merge=True :把恢复项 update 进现有 wrong_questions(第二本书如真题恢复,
                    与另一本书的错题共存不互相覆盖;题号不撞前提下安全)。
        """
        try:
            parts = code.split("~")
            if len(parts) != 3 or parts[0] != self.URL_CODE_PREFIX:
                return (0, "invalid")
            _, sig, b64 = parts
            if sig != self.bank_signature(ordered_ids):
                return (0, "stale")
            pad = "=" * (-len(b64) % 4)
            packed = zlib.decompress(base64.urlsafe_b64decode(b64 + pad))
        except Exception:
            return (0, "invalid")

        restored: dict[str, WrongQuestionRecord] = {}
        for i, qid in enumerate(ordered_ids):
            byte_i = i >> 2
            if byte_i >= len(packed):
                break
            code_val = (packed[byte_i] >> ((i & 3) * 2)) & 0b11
            if code_val == 0:
                continue
            if code_val == 1:
                restored[qid] = WrongQuestionRecord(question_id=qid, wrong_count=1, is_active_in_pool=True, subject=self.subject)
            elif code_val == 2:
                restored[qid] = WrongQuestionRecord(question_id=qid, wrong_count=2, is_active_in_pool=True, subject=self.subject)
            else:  # 3 历史
                restored[qid] = WrongQuestionRecord(question_id=qid, wrong_count=1, is_active_in_pool=False, subject=self.subject)
            # 统一保留本地已有记录的 added_at 与艾宾浩斯进度(单一入口,DS 审查要求),
            # 避免 URL 恢复整体替换时时间戳被刷成当前时间、进度被清空。
            self._keep_local_meta(restored[qid], self.wrong_questions.get(qid))

        if not restored:
            return (0, "empty")
        if merge:
            # 合并:不覆盖另一本书的错题(进度/时间已在循环内统一保留)
            for _qid, _rec in restored.items():
                self.wrong_questions[_qid] = _rec
        else:
            self.wrong_questions = restored
        self.save_state()
        return (len(restored), "ok")

    # =====================================================================
    # URL seen 位图:记录"已抽过的题",组卷时排除(每题 1 bit，比错题位图更省)。
    # 参数键 n1/n2/n3，与错题位图 d1/d2/d3 完全独立、共用 bank_signature。
    # 恢复语义是"合并"(seen 是单调累积集合，跨设备取并集)，不覆盖本地已有。
    # =====================================================================
    SEEN_CODE_PREFIX = "s1"

    def seen_to_url_code(self, ordered_ids: list[str]) -> str:
        """将当前科目"已抽过题"编码为可放进 URL 的紧凑串(1 bit/题)"""
        n = len(ordered_ids)
        packed = bytearray((n + 7) // 8)
        for i, qid in enumerate(ordered_ids):
            if qid in self.historical_seen_ids:
                packed[i >> 3] |= 1 << (i & 7)
        compressed = zlib.compress(bytes(packed), 9)
        b64 = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
        return f"{self.SEEN_CODE_PREFIX}~{self.bank_signature(ordered_ids)}~{b64}"

    def apply_seen_url_code(self, code: str, ordered_ids: list[str]) -> tuple[int, str]:
        """从 URL 串恢复"已抽过题"并**合并**进本地 seen 集合。返回 (恢复条数, 状态)。

        - 前缀/格式不符 -> (0, 'invalid')
        - 题库签名不匹配 -> (0, 'stale')
        - 空(全 0)      -> (0, 'empty')：不改动本地
        - 成功           -> (并入条数, 'ok')；seen 是累积集合，取并集而非覆盖
        """
        try:
            parts = code.split("~")
            if len(parts) != 3 or parts[0] != self.SEEN_CODE_PREFIX:
                return (0, "invalid")
            _, sig, b64 = parts
            if sig != self.bank_signature(ordered_ids):
                return (0, "stale")
            pad = "=" * (-len(b64) % 4)
            packed = zlib.decompress(base64.urlsafe_b64decode(b64 + pad))
        except Exception:
            return (0, "invalid")

        restored: set[str] = set()
        for i, qid in enumerate(ordered_ids):
            byte_i = i >> 3
            if byte_i >= len(packed):
                break
            if (packed[byte_i] >> (i & 7)) & 1:
                restored.add(qid)

        if not restored:
            return (0, "empty")
        self.historical_seen_ids |= restored  # 合并,不覆盖
        self.save_state()
        return (len(restored), "ok")

    # =====================================================================
    # URL 试卷编码：记住"上次生成的是哪几道题"，方便做完后跨设备查阅答案。
    # 规格无关：编码一个"试卷列表"，每份卷是变长的题号索引序列（索引=题号在 880
    # canonical 列表中的位置），故 5-3-3 / 自定义 / 3 套联考 全部统一支持。
    # 格式：p1~<sig>~<base64>，body = varint 结构：
    #   [卷数] 然后每份卷 [题数][idx*题数]，idx 用 2 字节小端（题库 <65536 题足够）。
    # sig 与位图共用 bank_signature，题库变化即判 stale，拒绝错位恢复。
    # =====================================================================
    PAPER_CODE_PREFIX = "p1"

    @staticmethod
    def encode_papers_code(papers_qids: list[list[str]], ordered_ids: list[str]) -> str:
        """把若干份试卷的题号列表编码为可放进 URL 的紧凑串。

        papers_qids: 每份卷一个题号列表（保持卷内原始顺序）。
        题号不在 canonical 列表中的会被跳过。
        """
        index_of = {qid: i for i, qid in enumerate(ordered_ids)}
        out = bytearray()
        out.append(min(len(papers_qids), 255))
        for qids in papers_qids:
            idxs = [index_of[q] for q in qids if q in index_of]
            out.append(min(len(idxs), 255))
            for idx in idxs[:255]:
                out += int(idx).to_bytes(2, "little")
        compressed = zlib.compress(bytes(out), 9)
        b64 = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
        sig = StateManager.bank_signature(ordered_ids)
        return f"{StateManager.PAPER_CODE_PREFIX}~{sig}~{b64}"

    @staticmethod
    def decode_papers_code(code: str, ordered_ids: list[str]) -> tuple[list[list[str]], str]:
        """从 URL 串还原试卷题号列表。返回 (每份卷的题号列表, 状态说明)。

        - 前缀/格式不符 -> ([], 'invalid')
        - 题库签名不匹配 -> ([], 'stale')
        - 成功 -> (papers_qids, 'ok')
        """
        try:
            parts = code.split("~")
            if len(parts) != 3 or parts[0] != StateManager.PAPER_CODE_PREFIX:
                return ([], "invalid")
            _, sig, b64 = parts
            if sig != StateManager.bank_signature(ordered_ids):
                return ([], "stale")
            pad = "=" * (-len(b64) % 4)
            data = zlib.decompress(base64.urlsafe_b64decode(b64 + pad))
        except Exception:
            return ([], "invalid")

        papers: list[list[str]] = []
        pos = 0
        if pos >= len(data):
            return ([], "invalid")
        n_papers = data[pos]; pos += 1
        n_ids = len(ordered_ids)
        for _ in range(n_papers):
            if pos >= len(data):
                break
            cnt = data[pos]; pos += 1
            qids: list[str] = []
            for _ in range(cnt):
                if pos + 2 > len(data):
                    break
                idx = int.from_bytes(data[pos:pos + 2], "little"); pos += 2
                if 0 <= idx < n_ids:
                    qids.append(ordered_ids[idx])
            papers.append(qids)
        return (papers, "ok")


    # =====================================================================
    # 艾宾浩斯错题调度（v2）
    # ---------------------------------------------------------------------
    # 每日错题任务不再随机抽，而是按"到期该复习"驱动：
    #   到期(next_review_at<=今天)优先 -> 顽固题(wrong_count>=阈值)补足 -> 新错题补足
    #   不足 target 不硬凑。做对 stage+1 延长间隔；做错回退/顽固强制明天。
    # 时间统一用本地日期 YYYY-MM-DD（_parse_date 容错解析老数据）。
    # =====================================================================
    EBBINGHAUS_INTERVALS = (1, 2, 4, 7, 15, 30)  # stage 0~5 -> 下次复习间隔(天)
    STUBBORN_THRESHOLD = 3  # wrong_count 达到此值视为顽固题
    ARCHIVE_STAGE = 6  # 做对推进到该阶段 -> 归档（is_active_in_pool=False）
    TAG_PRIORITY = {"方法不会": 20, "概念模糊": 15, "审题错误": 10, "计算失误": 8, "其他": 0}

    @staticmethod
    def _parse_date(s: str):
        """容错解析日期：支持 YYYY-MM-DD 或 ISO 时间戳；失败返回 None（不抛异常）。"""
        if not s:
            return None
        text = str(s).strip()[:10]
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except Exception:
            return None

    def _today(self):
        return datetime.now().date()

    @staticmethod
    def _stable_hash(text: str) -> int:
        h = 0
        for ch in text:
            h = (h * 31 + ord(ch)) % 100000
        return h

    def _legacy_due_date(self, rec, today):
        """老数据(next_review_at 空)到期兜底：added_at+1+稳定散列(0~13)天。

        避免 100 道老错题全压同一天到期（饥饿），摊到 14 天窗口逐日滚动；
        同一 qid 每天计算结果一致，不写盘、无迁移成本。
        """
        base = self._parse_date(rec.added_at) or today
        if base >= today:
            return base + timedelta(days=1)
        return base + timedelta(days=1 + (self._stable_hash(rec.question_id) % 14))

    def _due_date(self, rec, today):
        """统一到期日：优先 next_review_at，老数据走摊平兜底。"""
        nxt = self._parse_date(rec.next_review_at)
        if nxt is None:
            nxt = self._legacy_due_date(rec, today)
        return nxt

    def _wrong_priority(self, qid: str, today) -> float:
        """优先级分：逾期天数x10 + 错误次数x5 + 错误标签分（越高越先复习）。"""
        rec = self.wrong_questions.get(qid)
        if not rec:
            return 0.0
        nxt = self._due_date(rec, today)
        overdue = max(0, (today - nxt).days)
        return overdue * 10 + rec.wrong_count * 5 + self.TAG_PRIORITY.get(rec.error_tag, 0)

    def get_assigned_today(self, today=None) -> list:
        """返回今天已安排的错题 qid 列表（持久化；刷新后恢复同一份安排，不重复生成新题）。"""
        today = today or self._today()
        return list(self._assigned_daily.get(today.isoformat(), []))

    def mark_assigned_today(self, question_ids, today=None) -> int:
        """把 qid 记入"今日已安排"并持久化（再开 10 道=追加并集）；只保留当天 key。"""
        today = today or self._today()
        today_s = today.isoformat()
        merged = list(self._assigned_daily.get(today_s, []))
        for q in question_ids:
            if q not in merged:
                merged.append(q)
        self._assigned_daily = {today_s: merged}  # 保序：首次 select 优先级顺序 + 再开追加顺序
        self.save_state()
        return len(merged)

    def get_processed_today(self, today=None) -> set:
        """返回今天已处理（做对/又错/没做/清空）的 qid 集合（持久化，刷新不失效）。"""
        today = today or self._today()
        return set(self._processed_daily.get(today.isoformat(), []))

    def mark_processed_today(self, question_ids, today=None) -> int:
        """把 qid 记入"今日已处理"集合并持久化；只保留当天 key，旧日期自动清理。"""
        today = today or self._today()
        today_s = today.isoformat()
        cur = set(self._processed_daily.get(today_s, []))
        cur.update(question_ids)
        self._processed_daily = {today_s: sorted(cur)}  # 只保留今天，防无限增长
        self.save_state()
        return len(cur)

    def select_daily_wrong(
        self,
        target: int = 10,
        today=None,
        max_stubborn: int = 5,
        max_new: int = 3,  # 保留参数（兼容调用方）；新错题补足实际以"剩余缺口"为准，不足 target 时才启用
        exclude_ids: set | None = None,
        chapter_of: dict | None = None,
    ) -> list:
        """艾宾浩斯选题 + 考点轮转：到期优先 -> 顽固题补足(上限) -> 新错题补足(上限) -> 不硬凑。

        chapter_of: {qid: 章节} 映射。传入时按章节分组、轮流取题，使 target 道题覆盖尽量多考点
        （每章先各取一道，再第二轮补足；同章内仍按优先级顺序）。不传时退化为纯优先级顺序。
        返回 qid 列表（已去重）。到期题填满 target；不足时依次用顽固/新错题补。
        注意：启用 chapter_of 后，"考点覆盖"优先于"跨章紧急度"——不同章节的到期题按轮转次序展示，
        同章节内仍严格按到期优先级排序。这是有意 trade-off（用户要求每日错题覆盖多考点）。
        """
        today = today or self._today()
        exclude = set(exclude_ids or ())
        chapter_of = chapter_of or {}
        active = {
            qid for qid, rec in self.wrong_questions.items()
            if rec.is_active_in_pool and rec.wrong_count > 0
        }
        due = []
        stubborn = []
        newbie = []
        for qid in active:
            rec = self.wrong_questions[qid]
            if self._due_date(rec, today) <= today:
                due.append(qid)
            elif rec.wrong_count >= self.STUBBORN_THRESHOLD:
                stubborn.append(qid)
            elif rec.review_stage == 0 and not rec.last_reviewed_at:
                newbie.append(qid)
        due.sort(key=lambda q: (-self._wrong_priority(q, today),
                                self._parse_date(self.wrong_questions[q].added_at) or today))
        stubborn.sort(key=lambda q: -self.wrong_questions[q].wrong_count)
        newbie.sort(key=lambda q: self._parse_date(self.wrong_questions[q].added_at) or today)

        def _rotate(pool: list, limit: int, skip: set) -> list:
            """按章节轮流取题：每章先取队首（同章内保持传入的优先级顺序），再循环补足，覆盖考点最大化。"""
            buckets: dict[str, list] = {}
            for qid in pool:
                if qid in skip:
                    continue
                buckets.setdefault(chapter_of.get(qid, ""), []).append(qid)
            cursors = {ch: 0 for ch in buckets}
            picked: list = []
            while len(picked) < limit:
                advanced = False
                for ch, bucket in buckets.items():
                    if len(picked) >= limit:
                        break
                    if cursors[ch] < len(bucket):
                        picked.append(bucket[cursors[ch]])
                        cursors[ch] += 1
                        advanced = True
                if not advanced:
                    break
            return picked

        # due/stubborn/newbie 三池互斥（if/elif/elif），选中集内不可能混入对方池的题，
        # 补足上限直接用 max_stubborn / max_new 即可。
        # 1) 到期题：考点轮转填满 target（每章先取一道，再循环补足）
        selected = _rotate(due, target, exclude)
        seen = set(selected)
        # 2) 顽固题补足（上限 max_stubborn，考点轮转）
        for qid in _rotate(stubborn, min(target - len(selected), max_stubborn), seen | exclude):
            if len(selected) >= target:
                break
            selected.append(qid)
            seen.add(qid)
        # 3) 新错题补足（上限=剩余缺口：错题本初期全是"从未复习的新题"时，到期/顽固池为空，
        #    若沿用固定 max_new=3 每天只能推 3 道，凑不满 target；改为补足缺口，池空自然停止）
        for qid in _rotate(newbie, target - len(selected), seen | exclude):
            if len(selected) >= target:
                break
            selected.append(qid)
            seen.add(qid)
        return selected

    def record_review_result(self, question_id: str, correct: bool, today=None) -> bool:
        """艾宾浩斯复习回写。

        - 做对：stage+1，间隔翻倍；stage 达 ARCHIVE_STAGE -> 归档。
        - 做错：wrong_count+1；顽固题(wrong_count>=阈值且 stage>0)强制回 0（明天再练）；
                否则 stage 回退 1 级；归档题做错重新激活。
        - 幂等：同一题同一天只能回写一次（last_reviewed_at==今天 -> 返回 False）。
        - qid 不存在 -> 静默返回 False。
        """
        rec = self.wrong_questions.get(question_id)
        if not rec:
            return False
        today = today or self._today()
        today_s = today.isoformat()
        if rec.last_reviewed_at == today_s and rec.is_active_in_pool:
            return False  # 今日已复习过，幂等保护（归档题当天做错仍可重激活）
        rec.last_reviewed_at = today_s
        if correct:
            new_stage = rec.review_stage + 1
            if new_stage >= self.ARCHIVE_STAGE:
                rec.review_stage = self.ARCHIVE_STAGE
                rec.is_active_in_pool = False  # 归档
                rec.next_review_at = ""
            else:
                rec.review_stage = new_stage
                rec.next_review_at = (today + timedelta(days=self.EBBINGHAUS_INTERVALS[new_stage])).isoformat()
        else:
            rec.wrong_count += 1
            if not rec.is_active_in_pool:
                # 归档题做错：重新激活并从头开始（间隔回到 1 天，高频回炉）
                rec.is_active_in_pool = True
                rec.review_stage = 0
            elif rec.wrong_count >= self.STUBBORN_THRESHOLD and rec.review_stage > 0:
                rec.review_stage = 0  # 顽固题强制回 0
            else:
                rec.review_stage = max(0, rec.review_stage - 1)
            rec.next_review_at = (today + timedelta(days=1)).isoformat()
        self.save_state()
        return True

    def mark_wrong_not_done(self, question_id: str, today=None) -> bool:
        """标记"今天没做"：保持到期状态，明天继续推。

        不改变复习阶段、错误次数与 last_reviewed_at（不占用今天的复习记录），
        仅把 next_review_at 拉回今天，使明天 select_daily_wrong 的到期筛选仍能选中它。
        """
        rec = self.wrong_questions.get(question_id)
        if not rec or not rec.is_active_in_pool:
            return False  # 不存在或已归档：不做任何操作
        today = today or self._today()
        rec.next_review_at = today.isoformat()
        self.save_state()
        return True

    def postpone_review(self, question_ids: list, days: int = 1, today=None) -> int:
        """把指定错题的 next_review_at 顺延 days 天（"清空今日"等主动结束操作）。

        不改变复习阶段、错误次数与 last_reviewed_at；持久化写入状态文件，
        刷新/重开会话后仍生效；顺延到期后 select_daily_wrong 会自动重新选中。
        返回实际处理数。
        """
        today = today or self._today()
        nxt = (today + timedelta(days=days)).isoformat()
        cnt = 0
        for qid in question_ids:
            rec = self.wrong_questions.get(qid)
            if rec and rec.is_active_in_pool:
                rec.next_review_at = nxt
                cnt += 1
        if cnt:
            self.save_state()
        return cnt

    def batch_register_wrong(
        self,
        question_ids: list,
        error_tag: str = "概念模糊",
        added_at: str = None,
        wrong_count: int = 1,
        valid_ids: set = None,
    ):
        """批量补登历史错题（纸质时代的错题）。

        - added_at 用用户填的真实做错日期（YYYY-MM-DD）；解析失败/未来日期 -> 按今天。
        - 已存在或题号不在 valid_ids 中 -> 跳过（不覆盖已有复习进度）。
        - 返回 (登记数, 跳过数)。
        """
        today = self._today()
        added = self._parse_date(added_at) or today
        if added > today:
            added = today
        registered = skipped = 0
        for qid in question_ids:
            if not qid:
                continue
            if valid_ids is not None and qid not in valid_ids:
                skipped += 1
                continue
            if qid in self.wrong_questions:
                skipped += 1
                continue
            self.wrong_questions[qid] = WrongQuestionRecord(
                question_id=qid,
                added_at=added.isoformat(),
                error_tag=error_tag,
                wrong_count=max(1, int(wrong_count)),
                is_active_in_pool=True,
                subject=self.subject,
                last_reviewed_at="",
                next_review_at=(added + timedelta(days=1 + (self._stable_hash(qid) % 14))).isoformat(),
                review_stage=0,
            )
            registered += 1
        if registered:
            self.save_state()
        return registered, skipped

    def get_due_wrong_count(self, today=None) -> int:
        """今日到期待复习的错题数（用于前端画像展示）。"""
        today = today or self._today()
        n = 0
        for rec in self.wrong_questions.values():
            if not (rec.is_active_in_pool and rec.wrong_count > 0):
                continue
            if self._due_date(rec, today) <= today:
                n += 1
        return n

