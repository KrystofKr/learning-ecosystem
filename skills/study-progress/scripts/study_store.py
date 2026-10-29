#!/usr/bin/env python3
"""Subject-scoped learning records and source passages; Python standard library."""

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile


class StudyError(ValueError):
    """An invalid request or unusable study record."""


def identifier(value):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*", value
    ):
        raise StudyError(f"Invalid identifier: {value!r}")
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise StudyError(f"{label} must be nonempty text")
    return value


def texts(value, label, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise StudyError(f"{label} must be a list{' with at least one item' if nonempty else ''}")
    for item in value:
        text(item, label)
    return value


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise StudyError("Date must use YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise StudyError("Invalid calendar date") from exc
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, value):
    fd, filename = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


def line_passages(value, prefix="line"):
    return [
        {"locator": f"{prefix}:{number}", "text": line}
        for number, line in enumerate(value.splitlines(), 1) if line.strip()
    ]


def effective_checkpoints(checkpoints):
    """Resolve append-only corrections at the original activity's position."""
    seen, roots, effective, superseded = {}, {}, {}, {}
    for record in checkpoints:
        checkpoint_id = identifier(record.get("checkpoint_id"))
        if checkpoint_id in seen:
            raise StudyError("Duplicate checkpoint identifier in history")
        if "correction_of" in record:
            target_id = identifier(record["correction_of"])
            if target_id not in seen:
                raise StudyError("correction_of must refer to an earlier checkpoint in this subject")
            target = seen[target_id]
            root = roots[target_id]
            if effective[root]["checkpoint_id"] != target_id:
                raise StudyError("correction_of must refer to the effective version of the activity")
            for field in ("topic_id", "lesson_id", "activity", "learner_answer"):
                if record.get(field) != target.get(field):
                    raise StudyError(f"Correction must preserve the original {field}")
            superseded[target_id] = checkpoint_id
        else:
            root = checkpoint_id
        seen[checkpoint_id] = record
        roots[checkpoint_id] = root
        effective[root] = record
    return list(effective.values()), superseded


def replay_topic(topic, checkpoints):
    """Rebuild the topic without treating a correction as a later attempt."""
    topic.update(status="unseen", misconceptions=[], review_on=None)
    for record in checkpoints:
        if record["topic_id"] != topic["topic_id"]:
            continue
        result = record["result"]
        if result == "unassessed":
            continue
        if result == "introduced":
            if topic["status"] == "unseen":
                topic["status"] = "introduced"
            if record.get("review_on") is not None:
                topic["review_on"] = record["review_on"]
        else:
            topic["status"] = "demonstrated" if result == "passed" and record["hints_used"] == 0 else "practicing"
            topic["misconceptions"] = record["misconceptions"]
            topic["review_on"] = record.get("review_on")


def extract_passages(data, suffix, source_path=None):
    """Keep original locators. Unsupported/unreadable input stays explicit."""
    try:
        if suffix.lower() in {".txt", ".md", ".rst", ".csv", ".tex"}:
            passages = line_passages(data.decode("utf-8-sig"))
        elif suffix.lower() == ".ipynb":
            notebook = json.loads(data.decode("utf-8-sig"))
            if (not isinstance(notebook, dict) or notebook.get("nbformat") != 4
                    or not isinstance(notebook.get("cells"), list)):
                raise StudyError("Expected a version 4 Jupyter notebook with a cells list")
            passages = []
            for number, cell in enumerate(notebook["cells"], 1):
                if not isinstance(cell, dict) or cell.get("cell_type") not in {"markdown", "code", "raw"}:
                    raise StudyError(f"Invalid notebook cell {number}")
                content = cell.get("source", "")
                if isinstance(content, list) and all(isinstance(line, str) for line in content):
                    content = "".join(content)
                if not isinstance(content, str):
                    raise StudyError(f"Invalid source in notebook cell {number}")
                if content.strip():
                    passages.append({"locator": f"cell:{number}", "cell_type": cell["cell_type"], "text": content})
        elif suffix.lower() == ".ppt":
            renderer = shutil.which("soffice") or shutil.which("libreoffice")
            local_renderer = Path(sys.executable).parent / "soffice"
            if renderer is None and local_renderer.is_file():
                renderer = str(local_renderer)
            if renderer is None:
                return [], "Legacy PPT extraction needs LibreOffice Impress (soffice)."
            if source_path is None or not Path(source_path).is_file():
                return [], "Legacy PPT extraction requires the existing original file path."
            source_path = Path(source_path).absolute()
            with tempfile.TemporaryDirectory(prefix="study-ppt-convert-") as directory:
                work = Path(directory)
                completed = subprocess.run(
                    [renderer, "-env:UserInstallation=" + (work / "profile").as_uri(),
                     "--headless", "--norestore", "--convert-to", "pptx",
                     "--outdir", str(work), str(source_path)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                )
                converted = work / (source_path.stem + ".pptx")
                if completed.returncode or not converted.is_file():
                    detail = completed.stderr.decode("utf-8", errors="replace")[-500:]
                    raise StudyError("LibreOffice PPT conversion failed: " + detail)
                passages, reason = extract_passages(converted.read_bytes(), ".pptx")
            return passages, reason or (
                "Text indexed through a temporary LibreOffice PPT-to-PPTX conversion; "
                "original PPT retained as the reference. Graphics and formulas need visual review."
            )
        elif suffix.lower() == ".pptx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
                relationships = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
                targets = {node.attrib["Id"]: node.attrib["Target"] for node in relationships}
                relation_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                slides = presentation.findall(".//{*}sldId")
                passages = []
                for number, slide in enumerate(slides, 1):
                    target = targets[slide.attrib[f"{{{relation_ns}}}id"]]
                    member = target.lstrip("/") if target.startswith("/") else "ppt/" + target
                    node = ET.fromstring(archive.read(member))
                    content = "\n".join(item.text or "" for item in node.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"))
                    if content.strip():
                        passages.append({"locator": f"slide:{number}", "text": content})
        elif suffix.lower() == ".docx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                node = ET.fromstring(archive.read("word/document.xml"))
                ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                passages = []
                for number, paragraph in enumerate(node.iter(f"{{{ns}}}p"), 1):
                    content = "".join(item.text or "" for item in paragraph.iter(f"{{{ns}}}t"))
                    if content.strip():
                        passages.append({"locator": f"paragraph:{number}", "text": content})
        elif suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                if not shutil.which("pdftotext"):
                    return [], "PDF extraction needs pypdf or pdftotext; provide a readable transcript."
                completed = subprocess.run(
                    ["pdftotext", "-layout", "-", "-"], input=data,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30,
                )
                pages = completed.stdout.decode("utf-8").split("\f")
            else:
                pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]
            passages = [
                {"locator": f"page:{number}", "text": content}
                for number, content in enumerate(pages, 1) if content.strip()
            ]
        else:
            return [], "No text extractor for this format; provide a UTF-8 transcript."
        return passages, None if passages else "No readable text found; scanned documents need OCR."
    except Exception as exc:
        return [], f"Text extraction failed ({type(exc).__name__}): {exc}"


class StudyStore:
    def __init__(self, root):
        root = Path(root).expanduser()
        if not root.is_absolute():
            raise StudyError("--root must be an absolute path")
        self.root = root.resolve()

    def directory(self, subject):
        identifier(subject)
        path = self.root / "subjects" / subject
        if path.resolve() != path:
            raise StudyError("Subject directories cannot be symlinks")
        return path

    def load(self, subject):
        path = self.directory(subject) / "state.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StudyError(f"Unknown subject: {subject}") from exc
        except json.JSONDecodeError as exc:
            raise StudyError(f"Invalid state JSON for {subject}") from exc
        self.validate(state, subject)
        return state

    @staticmethod
    def validate(state, subject):
        if not isinstance(state, dict) or type(state.get("schema_version")) is not int or state["schema_version"] != 1:
            raise StudyError("Unsupported state schema_version")
        if state.get("subject_id") != subject:
            raise StudyError("State subject_id does not match requested subject")
        text(state.get("name"), "name")
        texts(state.get("goals"), "goals", nonempty=True)
        for collection in ("topics", "sources"):
            if not isinstance(state.get(collection), dict):
                raise StudyError(f"Invalid {collection} collection")
            key = "topic_id" if collection == "topics" else "source_id"
            for item_id, item in state[collection].items():
                identifier(item_id)
                if not isinstance(item, dict) or item.get("subject_id") != subject or item.get(key) != item_id:
                    raise StudyError(f"Invalid identity in {collection}")
        if not isinstance(state.get("checkpoints"), list):
            raise StudyError("Invalid checkpoints collection")
        for item in state["checkpoints"]:
            if not isinstance(item, dict) or item.get("subject_id") != subject:
                raise StudyError("Invalid identity in checkpoints")
        effective_checkpoints(state["checkpoints"])

    @contextmanager
    def edit(self, subject, initializing=False):
        directory = self.directory(subject)
        if initializing:
            directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir():
            raise StudyError(f"Unknown subject: {subject}")
        with (directory / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if initializing:
                if (directory / "state.json").exists():
                    raise StudyError(f"Subject already exists: {subject}")
                state = {}
            else:
                state = self.load(subject)
            yield state
            self.validate(state, subject)
            atomic_json(directory / "state.json", state)

    def init(self, subject, name, goals):
        text(name, "name")
        texts(goals, "goals", nonempty=True)
        with self.edit(subject, initializing=True) as state:
            state.update(schema_version=1, subject_id=subject, name=name, goals=goals,
                         created_at=now(), topics={}, sources={}, checkpoints=[])
        return self.status(subject)

    def subjects(self):
        directory = self.root / "subjects"
        if not directory.exists():
            return []
        return [
            {"subject_id": state["subject_id"], "name": state["name"], "goals": state["goals"]}
            for path in sorted(directory.iterdir()) if (path / "state.json").is_file()
            for state in [self.load(path.name)]
        ]

    @staticmethod
    def get(state, collection, item_id):
        identifier(item_id)
        if item_id not in state[collection]:
            raise StudyError(f"Unknown {collection} identifier: {item_id}")
        return state[collection][item_id]

    def topic_add(self, subject, topic_id, title, objectives, prerequisites):
        identifier(topic_id)
        text(title, "title")
        texts(objectives, "objectives", nonempty=True)
        with self.edit(subject) as state:
            if topic_id in state["topics"]:
                raise StudyError(f"Topic already exists: {topic_id}")
            for item_id in prerequisites:
                self.get(state, "topics", item_id)
            topic = dict(subject_id=subject, topic_id=topic_id, title=title,
                         objectives=objectives, prerequisites=prerequisites, status="unseen",
                         misconceptions=[], review_on=None)
            state["topics"][topic_id] = topic
        return topic

    def asset(self, subject, relative):
        directory = self.directory(subject)
        path = directory / relative
        if not path.resolve().is_relative_to(directory) or path.resolve() != path:
            raise StudyError("Source file must stay inside its subject directory")
        return path

    def verify_source(self, subject, source):
        for key, hash_key in (("file", "sha256"), ("transcript_file", "transcript_sha256")):
            if key in source:
                path = self.asset(subject, source[key])
                if not path.is_file() or digest(path.read_bytes()) != source[hash_key]:
                    raise StudyError(f"Stored source changed or missing: {source['source_id']}")

    @staticmethod
    def source_summary(source):
        return {key: value for key, value in source.items() if key != "passages"}

    def source_add(self, subject, source_id, path, title, version, priority, transcript=None, note=""):
        identifier(source_id)
        text(title, "title")
        text(version, "version")
        if priority not in {"primary", "supplementary", "notes"}:
            raise StudyError("Invalid source priority")
        path = Path(path).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise StudyError("Source must be an existing absolute file path")
        if path.is_symlink() or path.resolve().is_relative_to(self.root / "subjects"):
            raise StudyError("Supply an unmanaged material file, not a symlink or an already registered asset")
        original = path.read_bytes()
        transcript_data = None
        if transcript is not None:
            text(note, "transcript provenance note")
            transcript_path = Path(transcript).expanduser()
            if not transcript_path.is_absolute() or not transcript_path.is_file():
                raise StudyError("Transcript must be an existing absolute file path")
            if (transcript_path.is_symlink()
                    or transcript_path.resolve().is_relative_to(self.root / "subjects")
                    or transcript_path.samefile(path)):
                raise StudyError("Transcript must be a separate unmanaged file")
            transcript_data = transcript_path.read_bytes()
            passages = line_passages(transcript_data.decode("utf-8-sig"), "transcript-line")
            extraction_note = None if passages else "Transcript contains no readable text."
        else:
            passages, extraction_note = extract_passages(original, path.suffix, source_path=path)
        transfers = []
        try:
            with self.edit(subject) as state:
                if source_id in state["sources"]:
                    raise StudyError("Source ID already exists; use a new ID for a new version")
                relative = f"sources/{source_id}/{path.name}"
                stored = self.asset(subject, relative)
                pending = [(path, stored, digest(original))]
                source = dict(subject_id=subject, source_id=source_id, title=title, version=version,
                              priority=priority, original_path=str(path), file=relative,
                              ingest_mode="move", sha256=digest(original), topic_ids=[],
                              indexed=bool(passages), extraction_note=extraction_note,
                              note=note, passages=passages, created_at=now())
                if transcript_data is not None:
                    relative_text = f"sources/{source_id}/transcript/{transcript_path.name}"
                    pending.append((transcript_path, self.asset(subject, relative_text), digest(transcript_data)))
                    source.update(transcript_file=relative_text, transcript_sha256=digest(transcript_data))
                for incoming, destination, expected_hash in pending:
                    if destination.exists():
                        raise StudyError(f"Destination already exists: {destination}")
                    if digest(incoming.read_bytes()) != expected_hash:
                        raise StudyError("Incoming material changed during import")
                for incoming, destination, expected_hash in pending:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    transfers.append((incoming, destination))
                    shutil.move(str(incoming), str(destination))
                    if digest(destination.read_bytes()) != expected_hash:
                        raise StudyError("Moved material does not match the indexed content")
                state["sources"][source_id] = source
        except Exception:
            for incoming, destination in reversed(transfers):
                if destination.exists():
                    if incoming.exists():
                        # A failed cross-filesystem move may leave a partial destination.
                        destination.unlink()
                    else:
                        shutil.move(str(destination), str(incoming))
            raise
        return self.source_summary(source)

    def source_link(self, subject, source_id, topic_id):
        with self.edit(subject) as state:
            self.get(state, "topics", topic_id)
            source = self.get(state, "sources", source_id)
            if topic_id not in source["topic_ids"]:
                source["topic_ids"].append(topic_id)
        return self.source_summary(source)

    def sources(self, subject):
        return [self.source_summary(source) for source in self.load(subject)["sources"].values()]

    def passages(self, subject, source_id, start=0, limit=10):
        source = self.get(self.load(subject), "sources", source_id)
        self.verify_source(subject, source)
        return dict(subject_id=subject, source=self.source_summary(source),
                    total=len(source["passages"]), passages=source["passages"][start:start + limit])

    def search(self, subject, query, topic_id=None, start=0, limit=10):
        text(query, "query")
        state = self.load(subject)
        if topic_id is not None:
            self.get(state, "topics", topic_id)
        matches, unindexed = [], []
        priorities = {"primary": 0, "supplementary": 1, "notes": 2}
        for source in sorted(state["sources"].values(), key=lambda item: priorities[item["priority"]]):
            if topic_id is not None and topic_id not in source["topic_ids"]:
                continue
            self.verify_source(subject, source)
            if not source["indexed"]:
                unindexed.append(self.source_summary(source))
            for index, passage in enumerate(source["passages"]):
                if query.casefold() in passage["text"].casefold():
                    matches.append(dict(subject_id=subject, source_id=source["source_id"],
                                        title=source["title"], version=source["version"],
                                        priority=source["priority"], **passage,
                                        context=source["passages"][max(0, index - 1):index + 2]))
        return dict(subject_id=subject, total=len(matches), matches=matches[start:start + limit],
                    unindexed_sources=unindexed)

    def checkpoint(self, subject, record):
        required = {"subject_id", "checkpoint_id", "lesson_id", "topic_id", "activity_kind", "activity",
                    "learner_answer", "evaluation", "result", "hints_used", "difficulty",
                    "misconceptions", "source_refs", "next_step"}
        if not isinstance(record, dict) or not required.issubset(record) or set(record) - required - {"review_on", "correction_of"}:
            raise StudyError("Checkpoint has missing or unknown fields")
        if record["subject_id"] != subject:
            raise StudyError("Checkpoint subject_id does not match requested subject")
        for field in ("checkpoint_id", "lesson_id", "topic_id"):
            identifier(record[field])
        for field in ("activity", "evaluation", "difficulty", "next_step"):
            text(record[field], field)
        if record["result"] not in {"introduced", "needs-practice", "passed", "unassessed"}:
            raise StudyError("Invalid checkpoint result")
        if not isinstance(record["learner_answer"], str) or (
            record["result"] not in {"introduced", "unassessed"} and not record["learner_answer"].strip()
        ):
            raise StudyError("Assessed activities require an actual learner answer")
        if type(record["hints_used"]) is not int or record["hints_used"] < 0:
            raise StudyError("hints_used must be a nonnegative integer")
        texts(record["misconceptions"], "misconceptions")
        if record["result"] == "unassessed" and record["misconceptions"]:
            raise StudyError("Unassessed activities cannot establish misconceptions")
        if "correction_of" in record:
            identifier(record["correction_of"])
        if not isinstance(record["source_refs"], list):
            raise StudyError("source_refs must be a list")
        if record["activity_kind"] not in {"material", "diagnostic", "supplement"}:
            raise StudyError("Invalid activity_kind")
        if record["activity_kind"] == "material" and not record["source_refs"]:
            raise StudyError("Material-based activities require a source citation")
        if record.get("review_on") is not None:
            day(record["review_on"])
        record = dict(record, review_on=record.get("review_on"))
        with self.edit(subject) as state:
            topic = self.get(state, "topics", record["topic_id"])
            for saved in state["checkpoints"]:
                if saved["checkpoint_id"] == record["checkpoint_id"]:
                    if {key: value for key, value in saved.items() if key != "created_at"} != record:
                        raise StudyError("Checkpoint ID already used with different content")
                    return dict(subject_id=subject, checkpoint_id=record["checkpoint_id"],
                                duplicate=True, topic_status=topic["status"])
            for ref in record["source_refs"]:
                if not isinstance(ref, dict) or set(ref) != {"source_id", "locator"}:
                    raise StudyError("Citation requires source_id and locator")
                source = self.get(state, "sources", ref["source_id"])
                if record["topic_id"] not in source["topic_ids"]:
                    raise StudyError("Cited source is not linked to this topic")
                self.verify_source(subject, source)
                if ref["locator"] not in {item["locator"] for item in source["passages"]}:
                    raise StudyError("Citation locator does not exist")
            state["checkpoints"].append(dict(record, created_at=now()))
            effective, _ = effective_checkpoints(state["checkpoints"])
            replay_topic(topic, effective)
        return dict(subject_id=subject, checkpoint_id=record["checkpoint_id"],
                    duplicate=False, topic_status=topic["status"])

    def status(self, subject):
        state = self.load(subject)
        effective, _ = effective_checkpoints(state["checkpoints"])
        return dict(subject_id=subject, name=state["name"], goals=state["goals"],
                    topics=list(state["topics"].values()), source_count=len(state["sources"]),
                    checkpoint_count=len(state["checkpoints"]), activity_count=len(effective),
                    correction_count=len(state["checkpoints"]) - len(effective))

    def resume(self, subject, on, topic_id=None):
        day(on)
        state = self.load(subject)
        topics = list(state["topics"].values()) if topic_id is None else [self.get(state, "topics", topic_id)]
        history = [item for item in state["checkpoints"] if topic_id is None or item["topic_id"] == topic_id]
        effective, _ = effective_checkpoints(history)
        corrections = [item for item in history if "correction_of" in item]
        return dict(subject_id=subject, name=state["name"], goals=state["goals"], topics=topics,
                    last_checkpoint=effective[-1] if effective else None,
                    last_correction=corrections[-1] if corrections else None,
                    due_reviews=[item for item in topics if item["review_on"] and item["review_on"] <= on],
                    source_count=len(state["sources"]))

    def history(self, subject, topic_id=None, start=0, limit=10):
        state = self.load(subject)
        if topic_id is not None:
            self.get(state, "topics", topic_id)
        items = [item for item in state["checkpoints"] if topic_id is None or item["topic_id"] == topic_id]
        _, superseded = effective_checkpoints(items)
        items = [dict(item, is_effective=item["checkpoint_id"] not in superseded,
                      superseded_by=superseded.get(item["checkpoint_id"])) for item in items]
        return dict(subject_id=subject, total=len(items), checkpoints=items[start:start + limit])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Absolute path to the study data directory")
    parser.add_argument("--subject", help="Explicit subject ID (required except for subjects)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("subjects")
    init = commands.add_parser("init")
    init.add_argument("--name", required=True)
    init.add_argument("--goal", action="append", required=True)
    topic = commands.add_parser("topic-add")
    topic.add_argument("--topic", required=True)
    topic.add_argument("--title", required=True)
    topic.add_argument("--objective", action="append", required=True)
    topic.add_argument("--prerequisite", action="append", default=[])
    source = commands.add_parser("source-add")
    source.add_argument("--source", required=True)
    source.add_argument("--path", required=True)
    source.add_argument("--title", required=True)
    source.add_argument("--version", required=True)
    source.add_argument("--priority", choices=["primary", "supplementary", "notes"], default="primary")
    source.add_argument("--text", help="UTF-8 transcript for an otherwise unreadable original")
    source.add_argument("--note", default="")
    link = commands.add_parser("source-link")
    link.add_argument("--source", required=True)
    link.add_argument("--topic", required=True)
    commands.add_parser("sources")
    for name in ("passages", "search", "history"):
        command = commands.add_parser(name)
        command.add_argument("--start", type=int, default=0)
        command.add_argument("--limit", type=int, default=10)
        if name == "passages":
            command.add_argument("--source", required=True)
        else:
            command.add_argument("--topic")
        if name == "search":
            command.add_argument("--query", required=True)
    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("--input", required=True, help="Checkpoint JSON file")
    commands.add_parser("status")
    resume = commands.add_parser("resume")
    resume.add_argument("--on", required=True, help="User's local date, YYYY-MM-DD")
    resume.add_argument("--topic")
    args = parser.parse_args(argv)
    if args.command != "subjects" and not args.subject:
        parser.error("--subject is required for this command")
    if hasattr(args, "start") and (args.start < 0 or not 1 <= args.limit <= 100):
        parser.error("--start must be nonnegative and --limit must be between 1 and 100")
    try:
        store = StudyStore(args.root)
        subject = args.subject
        if args.command == "subjects":
            result = store.subjects()
        elif args.command == "init":
            result = store.init(subject, args.name, args.goal)
        elif args.command == "topic-add":
            result = store.topic_add(subject, args.topic, args.title, args.objective, args.prerequisite)
        elif args.command == "source-add":
            result = store.source_add(subject, args.source, args.path, args.title, args.version, args.priority, args.text, args.note)
        elif args.command == "source-link":
            result = store.source_link(subject, args.source, args.topic)
        elif args.command == "sources":
            result = store.sources(subject)
        elif args.command == "passages":
            result = store.passages(subject, args.source, args.start, args.limit)
        elif args.command == "search":
            result = store.search(subject, args.query, args.topic, args.start, args.limit)
        elif args.command == "checkpoint":
            result = store.checkpoint(subject, json.loads(Path(args.input).read_text(encoding="utf-8")))
        elif args.command == "status":
            result = store.status(subject)
        elif args.command == "resume":
            result = store.resume(subject, args.on, args.topic)
        else:
            result = store.history(subject, args.topic, args.start, args.limit)
    except (StudyError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
