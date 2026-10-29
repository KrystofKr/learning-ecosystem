"""Render one verified source PDF page for visual checking of formulas and maps."""
import math
import os
import tempfile

from .workspace import Workspace, EcosystemError


def render_page(root, subject, source_id, page_number, scale=2.0):
    if type(page_number) is not int or page_number < 1 or not 0.5 <= scale <= 3:
        raise EcosystemError('Use a 1-based page number and scale between 0.5 and 3')
    import pypdfium2
    workspace=Workspace(root)
    store=workspace.store()
    state=store.load(subject)
    source=store.get(state,'sources',source_id)
    store.verify_source(subject,source)
    original=store.asset(subject,source['file'])
    if original.suffix.lower()!='.pdf':
        raise EcosystemError('This visual renderer accepts PDF sources')
    with pypdfium2.PdfDocument(original) as document:
        if page_number > len(document):
            raise EcosystemError('PDF page does not exist')
        page=document[page_number-1]
        try:
            width,height=page.get_size()
            if not all(math.isfinite(value) and value > 0 for value in (width,height)):
                raise EcosystemError('Invalid PDF page dimensions')
            if width*height*scale*scale > 16_000_000:
                raise EcosystemError('Page is too large; use a smaller scale')
            bitmap=page.render(scale=scale)
            try:
                image=bitmap.to_pil()
                directory=workspace.data/'renders'/subject/source_id
                directory.mkdir(parents=True,exist_ok=True)
                target=directory/f'page-{page_number}-{source["sha256"][:12]}-{scale:g}.png'
                descriptor,name=tempfile.mkstemp(prefix='.render-',suffix='.png',dir=directory)
                os.close(descriptor)
                try:
                    image.save(name,format='PNG')
                    os.replace(name,target)
                finally:
                    from pathlib import Path
                    Path(name).unlink(missing_ok=True)
                return dict(subject_id=subject,source_id=source_id,page=page_number,
                            source_sha256=source['sha256'],image=str(target),
                            indexed_locator=any(item['locator']==f'page:{page_number}' for item in source.get('passages',[])))
            finally:
                bitmap.close()
        finally:
            page.close()
