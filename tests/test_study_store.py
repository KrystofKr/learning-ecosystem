"""Behavioral storage tests. All generated study data live in temporary directories."""

from concurrent.futures import ProcessPoolExecutor
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "skills/study-progress/scripts/study_store.py"
spec = importlib.util.spec_from_file_location("study_store", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
StudyStore, StudyError = module.StudyStore, module.StudyError


def assessment(subject="algebra-2026-zs", checkpoint_id="pokus-1", **changes):
    result = dict(subject_id=subject, checkpoint_id=checkpoint_id, lesson_id="lekce-1",
                  topic_id="zaklad", activity_kind="diagnostic", activity="Kolik je 2 + 3?",
                  learner_answer="5", evaluation="Správný výsledek bez pomoci.", result="passed",
                  hints_used=0, difficulty="zakladni", misconceptions=[], source_refs=[],
                  next_step="Ověřit náročnější příklad.", review_on="2026-10-18")
    result.update(changes)
    return result


def concurrent_checkpoint(root, index):
    return StudyStore(root).checkpoint("algebra-2026-zs", assessment(checkpoint_id=f"soub-{index}"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="study-skills-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "studium"
        self.store = StudyStore(self.root)
        self.subject = "algebra-2026-zs"
        self.store.init(self.subject, "Algebra", ["Samostatně řešit úlohy"])
        self.store.topic_add(self.subject, "zaklad", "Základ", ["Vyřešit základní úlohu"], [])

    def source(self, name="skripta", content="Sčítání\n\nSoučet dvou čísel.\n", subject=None):
        subject = subject or self.subject
        path = self.base / f"{subject}-{name}.md"
        path.write_text(content, encoding="utf-8")
        self.store.source_add(subject, name, path, "Skripta", "1", "primary")
        self.store.source_link(subject, name, "zaklad")
        return path

    def state_path(self):
        return self.root / "subjects" / self.subject / "state.json"

    def cli(self, *args, subject=True):
        command = [sys.executable, str(SCRIPT), "--root", str(self.root)]
        if subject:
            command.extend(["--subject", self.subject])
        return subprocess.run(command + list(args), capture_output=True, text=True)

    def test_subjects_do_not_mix_even_with_identical_local_ids(self):
        other = "statistika-2026-zs"
        self.store.init(other, "Statistika", ["Odhadovat pravděpodobnost"])
        self.store.topic_add(other, "zaklad", "Základ statistiky", ["Rozlišit jevy"], [])
        self.source(content="ALFA látka algebra")
        self.source(content="BETA látka statistika", subject=other)
        self.store.checkpoint(self.subject, assessment())
        self.store.checkpoint(other, assessment(subject=other, result="needs-practice", learner_answer="6",
                                               misconceptions=["Záměna pojmů"]))
        self.assertEqual(self.store.search(self.subject, "BETA")["total"], 0)
        self.assertEqual(self.store.search(other, "ALFA")["total"], 0)
        self.assertEqual(self.store.status(self.subject)["topics"][0]["status"], "demonstrated")
        self.assertEqual(self.store.status(other)["topics"][0]["status"], "practicing")
        self.assertEqual({item["subject_id"] for item in self.store.subjects()}, {self.subject, other})

    def test_new_process_resumes_last_saved_step_and_due_review(self):
        self.store.checkpoint(self.subject, assessment(next_step="Procvičit soustavy tří rovnic."))
        result = self.cli("resume", "--on", "2026-10-18")
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(result.stdout)
        self.assertEqual(saved["last_checkpoint"]["next_step"], "Procvičit soustavy tří rovnic.")
        self.assertEqual(saved["due_reviews"][0]["topic_id"], "zaklad")
        self.assertEqual(self.store.resume(self.subject, "2026-10-17")["due_reviews"], [])

    def test_hint_assisted_success_is_not_independent(self):
        self.store.checkpoint(self.subject, assessment(hints_used=1))
        self.assertEqual(self.store.status(self.subject)["topics"][0]["status"], "practicing")

    def test_explanation_is_introduced_not_demonstrated(self):
        self.store.checkpoint(self.subject, assessment(result="introduced", learner_answer=""))
        self.assertEqual(self.store.status(self.subject)["topics"][0]["status"], "introduced")

    def test_later_explanation_preserves_demonstrated_skill_and_review(self):
        self.store.checkpoint(self.subject, assessment())
        self.store.checkpoint(self.subject, assessment(checkpoint_id="pokus-2", result="introduced",
                                                      learner_answer="", review_on=None))
        topic = self.store.status(self.subject)["topics"][0]
        self.assertEqual(topic["status"], "demonstrated")
        self.assertEqual(topic["review_on"], "2026-10-18")

    def test_failed_review_reopens_practice_without_erasing_history(self):
        self.store.checkpoint(self.subject, assessment())
        self.store.checkpoint(self.subject, assessment(checkpoint_id="pokus-2", result="needs-practice",
                                                      misconceptions=["Chybný postup"], learner_answer="6"))
        self.assertEqual(self.store.status(self.subject)["topics"][0]["status"], "practicing")
        self.assertEqual(len(self.store.history(self.subject)["checkpoints"]), 2)

    def test_duplicate_retry_is_idempotent_and_conflicting_retry_fails(self):
        record = assessment()
        self.store.checkpoint(self.subject, record)
        self.assertTrue(self.store.checkpoint(self.subject, record)["duplicate"])
        with self.assertRaises(StudyError):
            self.store.checkpoint(self.subject, dict(record, learner_answer="6"))
        self.assertEqual(self.store.status(self.subject)["checkpoint_count"], 1)

    def test_course_convention_correction_removes_wrongly_attributed_error(self):
        self.source(content="F je množina koncových stavů, včetně q_ano a q_ne.\n")
        original = assessment(activity="Patří koncový zamítací q_ne do F?",
                              learner_answer="Ano, protože stroj ukončí výpočet.",
                              evaluation="Chybně použita jiná přijímací konvence.",
                              result="needs-practice", misconceptions=["Záměna přijetí a zastavení"])
        self.store.checkpoint(self.subject, original)
        original_saved = copy.deepcopy(self.store.load(self.subject)["checkpoints"][0])
        correction = dict(original, checkpoint_id="oprava-1", correction_of="pokus-1",
                          activity_kind="material", result="passed", misconceptions=[],
                          source_refs=[{"source_id": "skripta", "locator": "line:1"}],
                          evaluation="Podle doložené definice F zahrnuje oba koncové stavy; odpověď byla správná.")
        self.store.checkpoint(self.subject, correction)
        topic = self.store.resume(self.subject, "2026-10-18")["topics"][0]
        self.assertEqual(topic["status"], "demonstrated")
        self.assertEqual(topic["misconceptions"], [])
        self.assertEqual(self.store.load(self.subject)["checkpoints"][0], original_saved)
        counts = self.store.status(self.subject)
        self.assertEqual((counts["activity_count"], counts["correction_count"], counts["checkpoint_count"]), (1, 1, 2))

    def test_different_subject_conventions_and_corrections_do_not_mix(self):
        other = "jina-konvence"
        self.store.init(other, "Jiný kurz", ["Použít přijímací konvenci"])
        self.store.topic_add(other, "zaklad", "Stavy", ["Rozlišit F"], [])
        self.source(content="F obsahuje koncové stavy ANO i NE.")
        self.source(subject=other, content="F obsahuje pouze přijímací stavy ANO.")
        original = assessment(activity="Patří zamítací stav do F?", learner_answer="Ano",
                              result="needs-practice", misconceptions=["Chybně posouzené F"])
        self.store.checkpoint(self.subject, original)
        self.store.checkpoint(other, dict(original, subject_id=other))
        before_other = self.store.load(other)
        self.store.checkpoint(self.subject, dict(original, checkpoint_id="oprava-1", correction_of="pokus-1",
                              result="passed", misconceptions=[], activity_kind="material",
                              source_refs=[{"source_id": "skripta", "locator": "line:1"}]))
        self.assertEqual(self.store.load(other), before_other)
        self.assertEqual(self.store.search(self.subject, "ANO i NE")["total"], 1)
        self.assertEqual(self.store.search(other, "pouze přijímací")["total"], 1)

    def test_unverified_summary_can_be_saved_without_assessing_the_learner(self):
        self.source(content="Probrán model M=(Q,Sigma,Gamma,delta,q0,F).")
        self.store.checkpoint(self.subject, assessment(result="unassessed", learner_answer="F jsou koncové stavy",
                              evaluation="Souhrn význam F nedokládá; ověřit původní definici.",
                              review_on="2026-10-25"))
        topic = self.store.resume(self.subject, "2026-10-25")["topics"][0]
        self.assertEqual((topic["status"], topic["misconceptions"], topic["review_on"]), ("unseen", [], None))
        self.assertEqual(self.store.history(self.subject)["checkpoints"][0]["result"], "unassessed")

    def test_unassessed_activity_preserves_existing_evidence_and_review(self):
        self.store.checkpoint(self.subject, assessment())
        before = self.store.status(self.subject)["topics"][0]
        self.store.checkpoint(self.subject, assessment(checkpoint_id="neuzavreno", result="unassessed",
                              learner_answer="", review_on="2026-10-30"))
        self.assertEqual(self.store.status(self.subject)["topics"][0], before)

    def test_withdrawing_invalid_assessment_restores_previous_evidence(self):
        self.store.checkpoint(self.subject, assessment())
        bad = assessment(checkpoint_id="pokus-2", result="needs-practice", misconceptions=["Nedoložená chyba"],
                         review_on="2026-10-20")
        self.store.checkpoint(self.subject, bad)
        self.store.checkpoint(self.subject, dict(bad, checkpoint_id="oprava-2", correction_of="pokus-2",
                              result="unassessed", misconceptions=[], review_on=None,
                              evaluation="Původní kritérium nebylo ověřeno; zatím nelze rozhodnout."))
        topic = self.store.status(self.subject)["topics"][0]
        self.assertEqual((topic["status"], topic["misconceptions"], topic["review_on"]),
                         ("demonstrated", [], "2026-10-18"))

    def test_withdrawing_wrong_explanation_does_not_leave_material_introduced(self):
        original = assessment(result="introduced", learner_answer="", review_on="2026-10-20")
        self.store.checkpoint(self.subject, original)
        self.store.checkpoint(self.subject, dict(original, checkpoint_id="oprava-1", correction_of="pokus-1",
                              result="unassessed", evaluation="Výklad vycházel z nedoložené konvence."))
        topic = self.store.status(self.subject)["topics"][0]
        self.assertEqual((topic["status"], topic["review_on"]), ("unseen", None))

    def test_correction_of_older_activity_does_not_overwrite_later_attempt(self):
        original = assessment(result="needs-practice", misconceptions=["Stará chyba"])
        self.store.checkpoint(self.subject, original)
        later = assessment(checkpoint_id="pokus-2", result="needs-practice", learner_answer="6",
                           misconceptions=["Jiná pozdější obtíž"], review_on="2026-10-21")
        self.store.checkpoint(self.subject, later)
        before = self.store.status(self.subject)["topics"][0]
        self.store.checkpoint(self.subject, dict(original, checkpoint_id="oprava-1", correction_of="pokus-1",
                              result="passed", misconceptions=[], review_on="2026-11-01"))
        self.assertEqual(self.store.status(self.subject)["topics"][0], before)
        resumed = self.store.resume(self.subject, "2026-10-22")
        self.assertEqual(resumed["last_checkpoint"]["checkpoint_id"], "pokus-2")
        self.assertEqual(resumed["last_correction"]["checkpoint_id"], "oprava-1")
        self.assertEqual(resumed["due_reviews"][0]["review_on"], "2026-10-21")

    def test_correction_chain_retry_and_history_pagination(self):
        original = assessment(result="needs-practice", misconceptions=["Chyba učitele"])
        self.store.checkpoint(self.subject, original)
        first = dict(original, checkpoint_id="oprava-1", correction_of="pokus-1",
                     result="passed", misconceptions=[], hints_used=1)
        self.store.checkpoint(self.subject, first)
        second = dict(first, checkpoint_id="oprava-2", correction_of="oprava-1", hints_used=0)
        self.store.checkpoint(self.subject, second)
        self.assertTrue(self.store.checkpoint(self.subject, second)["duplicate"])
        history = self.store.history(self.subject)["checkpoints"]
        self.assertEqual([item["is_effective"] for item in history], [False, False, True])
        self.assertEqual([item["superseded_by"] for item in history], ["oprava-1", "oprava-2", None])
        first_page = self.store.history(self.subject, start=0, limit=1)["checkpoints"][0]
        self.assertFalse(first_page["is_effective"])
        self.assertEqual(first_page["superseded_by"], "oprava-1")
        self.assertEqual(self.store.status(self.subject)["activity_count"], 1)
        self.assertEqual(self.store.status(self.subject)["correction_count"], 2)
        self.assertEqual(self.store.resume(self.subject, "2026-10-18")["last_checkpoint"]["checkpoint_id"], "oprava-2")

    def test_corrections_reject_unknown_forward_self_and_invalid_targets(self):
        self.store.checkpoint(self.subject, assessment())
        before = self.state_path().read_bytes()
        for target in ("chybi", "oprava-1", None, "", [], 42, "../jiny"):
            with self.subTest(target=target), self.assertRaises(StudyError):
                self.store.checkpoint(self.subject, assessment(checkpoint_id="oprava-1", correction_of=target))
            self.assertEqual(self.state_path().read_bytes(), before)

    def test_corrections_cannot_change_original_activity_or_answer(self):
        self.store.checkpoint(self.subject, assessment())
        self.store.topic_add(self.subject, "jine-tema", "Jiné", ["Další cíl"], [])
        before = self.state_path().read_bytes()
        for changes in ({"activity": "Jiná otázka"}, {"learner_answer": "6"},
                        {"lesson_id": "nova-lekce"}, {"topic_id": "jine-tema"}):
            with self.subTest(changes=changes), self.assertRaises(StudyError):
                self.store.checkpoint(self.subject, assessment(checkpoint_id="oprava-1", correction_of="pokus-1", **changes))
            self.assertEqual(self.state_path().read_bytes(), before)

    def test_corrections_cannot_target_foreign_subject_or_superseded_record(self):
        other = "jiny-predmet"
        self.store.init(other, "Jiný", ["Jiná látka"])
        self.store.topic_add(other, "zaklad", "Základ", ["Jiný cíl"], [])
        self.store.checkpoint(other, assessment(subject=other, checkpoint_id="cizi-pokus"))
        with self.assertRaises(StudyError):
            self.store.checkpoint(self.subject, assessment(checkpoint_id="oprava-cizi", correction_of="cizi-pokus"))
        self.store.checkpoint(self.subject, assessment())
        self.store.checkpoint(self.subject, assessment(checkpoint_id="oprava-1", correction_of="pokus-1"))
        before = self.state_path().read_bytes()
        with self.assertRaises(StudyError):
            self.store.checkpoint(self.subject, assessment(checkpoint_id="oprava-vetev", correction_of="pokus-1"))
        self.assertEqual(self.state_path().read_bytes(), before)

    def test_correction_requires_valid_source_citation_and_atomic_commit(self):
        self.source()
        self.store.checkpoint(self.subject, assessment(result="needs-practice", misconceptions=["Původní chyba"]))
        before = self.state_path().read_bytes()
        correction = assessment(checkpoint_id="oprava-1", correction_of="pokus-1", activity_kind="material",
                                source_refs=[{"source_id": "skripta", "locator": "line:999"}])
        with self.assertRaises(StudyError):
            self.store.checkpoint(self.subject, correction)
        self.assertEqual(self.state_path().read_bytes(), before)
        correction["source_refs"][0]["locator"] = "line:1"
        with patch.object(module.os, "replace", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                self.store.checkpoint(self.subject, correction)
        self.assertEqual(self.state_path().read_bytes(), before)
        self.assertEqual(self.store.status(self.subject)["correction_count"], 0)

    def test_unassessed_records_cannot_attribute_misconceptions(self):
        before = self.state_path().read_bytes()
        with self.assertRaises(StudyError):
            self.store.checkpoint(self.subject, assessment(result="unassessed", misconceptions=["Neověřená chyba"]))
        self.assertEqual(self.state_path().read_bytes(), before)

    def test_correction_and_effective_state_survive_cli_restart(self):
        self.store.checkpoint(self.subject, assessment(result="needs-practice", misconceptions=["Chyba učitele"]))
        path = self.base / "oprava.json"
        path.write_text(json.dumps(assessment(checkpoint_id="oprava-1", correction_of="pokus-1")), encoding="utf-8")
        written = self.cli("checkpoint", "--input", str(path))
        self.assertEqual(written.returncode, 0, written.stderr)
        restarted = self.cli("resume", "--on", "2026-10-18")
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        result = json.loads(restarted.stdout)
        self.assertEqual(result["topics"][0]["status"], "demonstrated")
        self.assertEqual(result["topics"][0]["misconceptions"], [])
        self.assertEqual(result["last_checkpoint"]["checkpoint_id"], "oprava-1")
        self.assertEqual(result["last_correction"]["correction_of"], "pokus-1")
        history = json.loads(self.cli("history", "--limit", "1").stdout)
        self.assertFalse(history["checkpoints"][0]["is_effective"])

    def test_invalid_assessments_leave_previous_state_unchanged(self):
        before = self.state_path().read_bytes()
        for changes in (
            {"subject_id": "jiny-predmet"}, {"learner_answer": ""}, {"evaluation": ""},
            {"hints_used": -1}, {"hints_used": True}, {"review_on": "2026-02-30"},
            {"topic_id": "neexistuje"}, {"result": "mastered"}, {"activity_kind": "material"},
            {"misconceptions": "text"}, {"next_step": ""}, {"surprise": "value"},
        ):
            with self.subTest(changes=changes), self.assertRaises(StudyError):
                self.store.checkpoint(self.subject, assessment(**changes))
            self.assertEqual(self.state_path().read_bytes(), before)

    def test_citations_require_existing_topic_link_and_locator(self):
        self.source()
        record = assessment(activity_kind="material", source_refs=[{"source_id": "skripta", "locator": "line:3"}])
        self.store.checkpoint(self.subject, record)
        for locator in ("page:3", "line:2", "line:999"):
            bad = assessment(checkpoint_id="bad-citation", activity_kind="material",
                             source_refs=[{"source_id": "skripta", "locator": locator}])
            with self.subTest(locator=locator), self.assertRaises(StudyError):
                self.store.checkpoint(self.subject, bad)
        self.store.topic_add(self.subject, "jine-tema", "Jiné", ["Další cíl"], [])
        with self.assertRaises(StudyError):
            self.store.checkpoint(self.subject, dict(record, checkpoint_id="jiny-pokus", topic_id="jine-tema"))

    def test_search_preserves_line_numbers_and_surrounding_context(self):
        self.source()
        result = self.store.search(self.subject, "SOUČET", "zaklad")
        self.assertEqual(result["matches"][0]["locator"], "line:3")
        self.assertEqual(result["matches"][0]["context"][0]["locator"], "line:1")
        self.assertEqual(result["matches"][0]["version"], "1")

    def test_recreated_input_path_does_not_change_adopted_material(self):
        original = self.source()
        self.assertFalse(original.exists())
        original.write_text("Nový obsah", encoding="utf-8")
        self.assertEqual(self.store.search(self.subject, "součet")["total"], 1)

    def test_corrupt_adopted_material_is_detected(self):
        self.source()
        source = self.store.load(self.subject)["sources"]["skripta"]
        self.store.asset(self.subject, source["file"]).write_text("Přepsaný obsah", encoding="utf-8")
        with self.assertRaises(StudyError):
            self.store.search(self.subject, "součet")
        with self.assertRaises(StudyError):
            self.store.checkpoint(self.subject, assessment(source_refs=[{"source_id": "skripta", "locator": "line:3"}]))

    def test_new_source_versions_preserve_old_citations(self):
        self.source(content="Původní pravidlo", name="skripta-v1")
        self.source(content="Změněné pravidlo", name="skripta-v2")
        self.store.checkpoint(self.subject, assessment(activity_kind="material",
            source_refs=[{"source_id": "skripta-v1", "locator": "line:1"}]))
        self.assertEqual(self.store.passages(self.subject, "skripta-v1")["passages"][0]["text"], "Původní pravidlo")
        with self.assertRaises(StudyError):
            self.source(name="skripta-v1", content="Přepsat verzi")

    def test_unsupported_material_stays_explicitly_unindexed(self):
        path = self.base / "obrazek.png"
        path.write_bytes(b"not-readable-as-text")
        result = self.store.source_add(self.subject, "obrazek", path, "Obrázek", "1", "notes")
        self.assertFalse(result["indexed"])
        search = self.store.search(self.subject, "anything")
        self.assertEqual(search["total"], 0)
        self.assertEqual(search["unindexed_sources"][0]["source_id"], "obrazek")

    def test_transcript_retains_provenance_and_truthful_locators(self):
        path = self.base / "sken.pdf"
        path.write_bytes(b"scanned-binary-fixture")
        transcript = self.base / "prepis.txt"
        transcript.write_text("Ruční přepis\nObsah snímku", encoding="utf-8")
        result = self.store.source_add(self.subject, "sken", path, "Sken", "1", "primary",
                                      transcript=transcript, note="Ruční přepis uživatele")
        self.assertTrue(result["indexed"])
        self.assertFalse(path.exists())
        self.assertFalse(transcript.exists())
        self.assertIn("transcript_sha256", result)
        self.assertEqual(self.store.search(self.subject, "obsah")["matches"][0]["locator"], "transcript-line:2")
        self.store.asset(self.subject, result["transcript_file"]).write_text("Pozměněno")
        with self.assertRaises(StudyError):
            self.store.passages(self.subject, "sken")

    def test_source_import_moves_original_preserving_name_and_bytes(self):
        path = self.base / "Moje skripta.md"
        contents = "Materiál dodaný uživatelem.\n".encode("utf-8")
        path.write_bytes(contents)
        original_inode = path.stat().st_ino
        result = self.store.source_add(self.subject, "skripta", path, "Skripta", "1", "primary")
        adopted = self.store.asset(self.subject, result["file"])
        self.assertFalse(path.exists())
        self.assertEqual(adopted.name, path.name)
        self.assertEqual(adopted.read_bytes(), contents)
        self.assertEqual(adopted.stat().st_ino, original_inode)
        self.assertEqual(result["original_path"], str(path))
        self.assertEqual(result["ingest_mode"], "move")
        self.assertEqual([p for p in self.root.rglob('*.md')], [adopted])

    def test_failed_source_commit_restores_original_and_catalog(self):
        path = self.base / "skripta.md"
        path.write_text("Původní obsah", encoding="utf-8")
        before = self.state_path().read_bytes()
        with patch.object(module.os, "replace", side_effect=OSError("simulated commit failure")):
            with self.assertRaises(OSError):
                self.store.source_add(self.subject, "skripta", path, "Skripta", "1", "primary")
        self.assertEqual(path.read_text(encoding="utf-8"), "Původní obsah")
        self.assertEqual(self.state_path().read_bytes(), before)
        self.assertEqual(list(self.root.rglob('*.md')), [])

    def test_failed_commit_restores_both_original_and_transcript(self):
        path = self.base / "sken.pdf"
        path.write_bytes(b"scan")
        transcript = self.base / "prepis.txt"
        transcript.write_text("Přepis", encoding="utf-8")
        with patch.object(module.os, "replace", side_effect=OSError("simulated commit failure")):
            with self.assertRaises(OSError):
                self.store.source_add(self.subject, "sken", path, "Sken", "1", "primary",
                                      transcript=transcript, note="Ruční přepis")
        self.assertEqual(path.read_bytes(), b"scan")
        self.assertEqual(transcript.read_text(encoding="utf-8"), "Přepis")
        self.assertEqual(self.store.sources(self.subject), [])
        self.assertEqual(list(self.root.rglob('*.pdf')) + list(self.root.rglob('*.txt')), [])

    def test_move_failure_cleans_partial_destination_and_keeps_original(self):
        path = self.base / "skripta.md"
        path.write_text("Původní obsah", encoding="utf-8")

        def interrupted_move(incoming, destination):
            Path(destination).write_bytes(b"partial")
            raise OSError("simulated interrupted cross-filesystem copy")

        with patch.object(module.shutil, "move", side_effect=interrupted_move):
            with self.assertRaises(OSError):
                self.store.source_add(self.subject, "skripta", path, "Skripta", "1", "primary")
        self.assertEqual(path.read_text(encoding="utf-8"), "Původní obsah")
        self.assertEqual(list(self.root.rglob('*.md')), [])
        self.assertEqual(self.store.sources(self.subject), [])

    def test_failed_transcript_move_restores_material_already_moved(self):
        path = self.base / "sken.pdf"
        path.write_bytes(b"scan")
        transcript = self.base / "prepis.txt"
        transcript.write_text("Přepis", encoding="utf-8")
        real_move = module.shutil.move

        def fail_transcript(incoming, destination):
            if Path(incoming) == transcript:
                raise OSError("simulated transcript move failure")
            return real_move(incoming, destination)

        with patch.object(module.shutil, "move", side_effect=fail_transcript):
            with self.assertRaises(OSError):
                self.store.source_add(self.subject, "sken", path, "Sken", "1", "primary",
                                      transcript=transcript, note="Ruční přepis")
        self.assertEqual(path.read_bytes(), b"scan")
        self.assertEqual(transcript.read_text(encoding="utf-8"), "Přepis")
        self.assertEqual(self.store.sources(self.subject), [])

    def test_collision_does_not_move_input_or_replace_existing_file(self):
        path = self.base / "skripta.md"
        path.write_text("Nový materiál", encoding="utf-8")
        destination = self.store.asset(self.subject, "sources/skripta/skripta.md")
        destination.parent.mkdir(parents=True)
        destination.write_text("Existující soubor", encoding="utf-8")
        with self.assertRaises(StudyError):
            self.store.source_add(self.subject, "skripta", path, "Skripta", "1", "primary")
        self.assertEqual(path.read_text(encoding="utf-8"), "Nový materiál")
        self.assertEqual(destination.read_text(encoding="utf-8"), "Existující soubor")
        self.assertEqual(self.store.sources(self.subject), [])

    def test_already_managed_material_cannot_be_moved_into_another_subject(self):
        self.source()
        source = self.store.sources(self.subject)[0]
        material = self.store.asset(self.subject, source["file"])
        other = "statistika-2026-zs"
        self.store.init(other, "Statistika", ["Studovat statistiku"])
        with self.assertRaises(StudyError):
            self.store.source_add(other, "skripta", material, "Skripta", "1", "primary")
        self.assertTrue(material.is_file())
        self.assertEqual(self.store.search(self.subject, "součet")["total"], 1)
        self.assertEqual(self.store.sources(other), [])

    def test_missing_pdf_extractor_does_not_claim_readable_pdf(self):
        with patch.dict(sys.modules, {"pypdf": None}), patch.object(module.shutil, "which", return_value=None):
            passages, reason = module.extract_passages(b"%PDF-1.4\n", ".pdf")
        self.assertEqual(passages, [])
        self.assertIn("pypdf or pdftotext", reason)

    def test_pptx_uses_presentation_order_not_slide_filename_order(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="rId2"/><p:sldId id="2" r:id="rId1"/></p:sldIdLst></p:presentation>')
            archive.writestr("ppt/_rels/presentation.xml.rels", '<Relationships><Relationship Id="rId1" Target="slides/slide1.xml"/><Relationship Id="rId2" Target="slides/slide2.xml"/></Relationships>')
            for number, content in ((1, "Druhý snímek"), (2, "První snímek")):
                archive.writestr(f"ppt/slides/slide{number}.xml", f'<a:t xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">{content}</a:t>')
        path = self.base / "prezentace.pptx"
        path.write_bytes(buffer.getvalue())
        self.store.source_add(self.subject, "prezentace", path, "Prezentace", "1", "primary")
        passages = self.store.passages(self.subject, "prezentace")["passages"]
        self.assertEqual(passages, [{"locator": "slide:1", "text": "První snímek"},
                                    {"locator": "slide:2", "text": "Druhý snímek"}])

    def test_notebook_keeps_cell_positions_types_and_source_without_outputs(self):
        notebook = {"nbformat": 4, "cells": [
            {"cell_type": "markdown", "source": ["# Úvod\n", "Výklad."]},
            {"cell_type": "code", "source": []},
            {"cell_type": "code", "source": "print(2 + 3)",
             "outputs": [{"output_type": "stream", "text": ["ulozeny-vystup-xyz"]}]},
            {"cell_type": "raw", "source": ["Poznámka"]},
        ]}
        passages, reason = module.extract_passages(json.dumps(notebook).encode(), ".ipynb")
        self.assertIsNone(reason)
        self.assertEqual(passages, [
            {"locator": "cell:1", "cell_type": "markdown", "text": "# Úvod\nVýklad."},
            {"locator": "cell:3", "cell_type": "code", "text": "print(2 + 3)"},
            {"locator": "cell:4", "cell_type": "raw", "text": "Poznámka"},
        ])

    def test_notebook_import_does_not_execute_code_and_supports_cell_citation(self):
        marker = self.base / "must-not-be-created.txt"
        code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')"
        path = self.base / "cviceni.ipynb"
        path.write_text(json.dumps({"nbformat": 4, "cells": [
            {"cell_type": "markdown", "source": "# Tenzory"},
            {"cell_type": "code", "source": code},
        ]}), encoding="utf-8")
        result = self.store.source_add(self.subject, "cviceni", path, "Cvičení", "1", "primary")
        self.assertFalse(marker.exists())
        self.assertFalse(path.exists())
        self.assertTrue(result["indexed"])
        self.store.source_link(self.subject, "cviceni", "zaklad")
        matches = self.store.search(self.subject, "Path(", "zaklad")["matches"]
        self.assertEqual(matches[0]["locator"], "cell:2")
        self.assertEqual(matches[0]["cell_type"], "code")
        self.store.checkpoint(self.subject, assessment(activity_kind="material",
            source_refs=[{"source_id": "cviceni", "locator": "cell:2"}]))
        self.assertFalse(marker.exists())

    def test_invalid_notebook_is_explicitly_unindexed_without_partial_text(self):
        cases = [
            b"not-json", {"nbformat": 3, "cells": []}, {"nbformat": 4, "cells": "wrong"},
            {"nbformat": 4, "cells": [{"cell_type": "markdown", "source": "Valid"},
                                      {"cell_type": "code", "source": 42}]},
        ]
        for notebook in cases:
            data = notebook if isinstance(notebook, bytes) else json.dumps(notebook).encode()
            with self.subTest(notebook=notebook):
                passages, reason = module.extract_passages(data, ".ipynb")
                self.assertEqual(passages, [])
                self.assertTrue(reason)

    def test_legacy_ppt_reports_missing_converter(self):
        path = self.base / "prednaska.ppt"
        path.write_bytes(b"legacy-ppt")
        with patch.object(module.shutil, "which", return_value=None), patch.object(module.sys, "executable", "/nonexistent/python"):
            passages, reason = module.extract_passages(path.read_bytes(), ".ppt", source_path=path)
        self.assertEqual(passages, [])
        self.assertIn("LibreOffice", reason)
        self.assertEqual(path.read_bytes(), b"legacy-ppt")

    def test_legacy_ppt_conversion_keeps_slide_number_and_removes_temporary_output(self):
        path = self.base / "prednaska.ppt"
        path.write_bytes(b"legacy-ppt")
        converted_dirs = []

        def converter(command, **kwargs):
            directory = Path(command[command.index("--outdir") + 1])
            converted_dirs.append(directory)
            with zipfile.ZipFile(directory / "prednaska.pptx", "w") as archive:
                archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="rId1"/><p:sldId id="2" r:id="rId2"/></p:sldIdLst></p:presentation>')
                archive.writestr("ppt/_rels/presentation.xml.rels", '<Relationships><Relationship Id="rId1" Target="slides/slide1.xml"/><Relationship Id="rId2" Target="slides/slide2.xml"/></Relationships>')
                archive.writestr("ppt/slides/slide1.xml", '<root/>')
                archive.writestr("ppt/slides/slide2.xml", '<a:t xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">Asociační pravidla</a:t>')
            return subprocess.CompletedProcess(command, 0, stdout=b"converted", stderr=b"")

        with patch.object(module.shutil, "which", return_value="/fake/soffice"), patch.object(module.subprocess, "run", side_effect=converter):
            passages, reason = module.extract_passages(path.read_bytes(), ".ppt", source_path=path)
        self.assertEqual(passages, [{"locator": "slide:2", "text": "Asociační pravidla"}])
        self.assertIn("temporary LibreOffice", reason)
        self.assertTrue(converted_dirs)
        self.assertTrue(all(not p.exists() for p in converted_dirs))
        self.assertEqual(path.read_bytes(), b"legacy-ppt")

    def test_legacy_ppt_does_not_claim_success_when_converter_produces_no_file(self):
        path = self.base / "prednaska.ppt"
        path.write_bytes(b"legacy-ppt")
        with patch.object(module.shutil, "which", return_value="/fake/soffice"), patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")):
            passages, reason = module.extract_passages(path.read_bytes(), ".ppt", source_path=path)
        self.assertEqual(passages, [])
        self.assertIn("conversion failed", reason)
        self.assertEqual(path.read_bytes(), b"legacy-ppt")

    def test_docx_keeps_original_paragraph_numbers_including_empty(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><w:p><w:r><w:t>Poznámka</w:t></w:r></w:p></w:body></w:document>')
        passages, reason = module.extract_passages(buffer.getvalue(), ".docx")
        self.assertIsNone(reason)
        self.assertEqual(passages, [{"locator": "paragraph:2", "text": "Poznámka"}])

    def test_missing_subject_and_invalid_identifiers_create_no_data(self):
        before = set(self.root.rglob("*"))
        for subject in ("../statistika", "", "Algebra", "a/b", "a--b"):
            with self.subTest(subject=subject), self.assertRaises(StudyError):
                self.store.init(subject, "Název", ["Cíl"])
        with self.assertRaises(StudyError):
            self.store.status("neexistuje")
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_symlink_cannot_alias_two_subjects(self):
        alias = self.root / "subjects" / "jiny-predmet"
        alias.symlink_to(self.root / "subjects" / self.subject, target_is_directory=True)
        with self.assertRaises(StudyError):
            self.store.status("jiny-predmet")

    def test_unknown_schema_or_cross_subject_record_is_not_overwritten(self):
        original = self.store.load(self.subject)
        for corruption in ("version", "subject", "nested"):
            state = copy.deepcopy(original)
            if corruption == "version":
                state["schema_version"] = 99
            elif corruption == "subject":
                state["subject_id"] = "jiny-predmet"
            else:
                state["topics"]["zaklad"]["subject_id"] = "jiny-predmet"
            self.state_path().write_text(json.dumps(state), encoding="utf-8")
            before = self.state_path().read_bytes()
            with self.subTest(corruption=corruption), self.assertRaises(StudyError):
                self.store.checkpoint(self.subject, assessment())
            self.assertEqual(self.state_path().read_bytes(), before)

    def test_failed_atomic_commit_preserves_previous_checkpoint(self):
        self.store.checkpoint(self.subject, assessment())
        before = self.state_path().read_bytes()
        with patch.object(module.os, "replace", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                self.store.checkpoint(self.subject, assessment(checkpoint_id="pokus-2"))
        self.assertEqual(self.state_path().read_bytes(), before)
        self.assertEqual(list(self.state_path().parent.glob(".state-*.tmp")), [])
        self.assertEqual(StudyStore(self.root).status(self.subject)["checkpoint_count"], 1)

    def test_concurrent_processes_do_not_lose_updates(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(concurrent_checkpoint, [str(self.root)] * 12, range(12)))
        self.assertEqual(len(results), 12)
        saved = self.store.history(self.subject)["total"]
        self.assertEqual(saved, 12)

    def test_history_and_resume_can_target_one_topic(self):
        self.store.checkpoint(self.subject, assessment())
        self.store.topic_add(self.subject, "pokrocile", "Pokročilé", ["Složitější příklad"], ["zaklad"])
        self.store.checkpoint(self.subject, assessment(checkpoint_id="pokus-2", topic_id="pokrocile"))
        self.assertEqual(self.store.resume(self.subject, "2026-10-18", "zaklad")["last_checkpoint"]["checkpoint_id"], "pokus-1")
        self.assertEqual(self.store.history(self.subject, "pokrocile")["total"], 1)
        self.assertEqual(self.store.history(self.subject, start=1, limit=1)["checkpoints"][0]["checkpoint_id"], "pokus-2")

    def test_cli_rejects_missing_subject_and_bad_pagination(self):
        for arguments, with_subject in ((["status"], False), (["history", "--start", "-1"], True),
                                        (["history", "--limit", "101"], True)):
            with self.subTest(arguments=arguments):
                self.assertNotEqual(self.cli(*arguments, subject=with_subject).returncode, 0)

    def test_cli_round_trip_accepts_checkpoint_file_and_returns_json(self):
        path = self.base / "checkpoint.json"
        path.write_text(json.dumps(assessment(), ensure_ascii=False), encoding="utf-8")
        result = self.cli("checkpoint", "--input", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["topic_status"], "demonstrated")
        result = self.cli("subjects", subject=False)
        self.assertEqual(json.loads(result.stdout)[0]["subject_id"], self.subject)

    def test_global_profile_is_unchanged_by_subject_operations(self):
        profile = self.root / "profil.md"
        profile.write_text("Piš česky. Dávej jednu otázku najednou.\n", encoding="utf-8")
        before = profile.read_bytes()
        self.source()
        self.store.checkpoint(self.subject, assessment())
        self.store.resume(self.subject, "2026-10-18")
        self.assertEqual(profile.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
