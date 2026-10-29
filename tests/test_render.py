import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ecosystem/src'))
from learning_ecosystem.workspace import Workspace, EcosystemError
from learning_ecosystem.render import render_page


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('pypdf','pypdfium2','PIL')),
                     'PDF dependencies must be installed')
class RenderTests(unittest.TestCase):
    def setUp(self):
        from pypdf import PdfWriter
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'project'
        helper=self.root/'skills/study-progress/scripts/study_store.py'
        helper.parent.mkdir(parents=True)
        shutil.copy2(ROOT/'skills/study-progress/scripts/study_store.py',helper)
        self.workspace=Workspace(self.root)
        self.store=self.workspace.store()
        self.store.init('subject','Subject',['Read a diagram'])
        incoming=Path(self.tmp.name)/'diagram.pdf'
        writer=PdfWriter()
        writer.add_blank_page(width=72,height=144)
        with incoming.open('wb') as stream:
            writer.write(stream)
        self.store.source_add('subject','diagram',incoming,'Diagram','1','primary')

    def test_render_preserves_source_and_matches_page_dimensions(self):
        from PIL import Image
        source=self.store.sources('subject')[0]
        path=self.store.asset('subject',source['file'])
        before=path.read_bytes()
        result=render_page(self.root,'subject','diagram',1,2.0)
        with Image.open(result['image']) as image:
            self.assertEqual(image.size,(144,288))
        self.assertEqual(result['source_sha256'],source['sha256'])
        self.assertEqual(path.read_bytes(),before)

    def test_nonexistent_page_does_not_produce_a_fake_visual(self):
        with self.assertRaises(EcosystemError):
            render_page(self.root,'subject','diagram',2)
        self.assertFalse((self.workspace.data/'renders').exists())

    def test_changed_source_is_rejected_before_rendering(self):
        source=self.store.sources('subject')[0]
        path=self.store.asset('subject',source['file'])
        path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(ValueError):
            render_page(self.root,'subject','diagram',1)
        self.assertFalse((self.workspace.data/'renders').exists())


if __name__=='__main__':
    unittest.main()
