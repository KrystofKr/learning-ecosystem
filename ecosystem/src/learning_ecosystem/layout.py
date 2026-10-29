"""Restore coursework to its own real directory without links between repositories."""
import os
from pathlib import Path

from .setup import repair_runtime_paths
from .workspace import EcosystemError, atomic_json, locked, now


def restore_coursework(root, *, destination=None):
    root = Path(root).resolve()
    projects = root / 'projects'
    # Keep the literal destination: resolving the current alias would point back here.
    destination = Path(destination or root.parent / 'osu_repository').absolute()
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise EcosystemError('Coursework must be outside the ecosystem')
    with locked(root / 'ecosystem/data/.layout.lock'):
        if projects.is_symlink():
            raise EcosystemError('Unexpected coursework source link; nothing moved')
        if projects.exists():
            for name in ('studium', 'study-skills', 'docs'):
                path = projects / name
                if path.exists() or path.is_symlink():
                    raise EcosystemError('Remove the obsolete repository entry first: ' + str(path))
            if any(projects.glob('studium.before-ecosystem-*')):
                raise EcosystemError('An obsolete library backup is still in the coursework directory')
            old_link = None
            if destination.is_symlink():
                if destination.resolve() != projects:
                    raise EcosystemError('Destination points to unrelated data')
                old_link = os.readlink(destination)
            elif destination.exists():
                raise EcosystemError('Destination is already a real directory; nothing replaced')
            journal = dict(schema_version=1, status='prepared', coursework=str(destination),
                           ecosystem=str(root), started_at=now())
            atomic_json(root / 'ecosystem/data/layout.json', journal)
            if old_link is not None:
                destination.unlink()
            try:
                os.rename(projects, destination)
            except BaseException:
                if old_link is not None:
                    destination.symlink_to(old_link, target_is_directory=True)
                raise
        elif not destination.is_dir() or destination.is_symlink():
            raise EcosystemError('The original coursework directory is unavailable')
        # Resume here if the directory move succeeded before a previous interruption.
        runtime = destination / '.venv'
        repaired = repair_runtime_paths(destination, projects, environment=runtime) if runtime.is_dir() else dict(changed=[])
        forbidden = ('studium', 'study-skills', 'docs')
        if any((destination / name).exists() or (destination / name).is_symlink() for name in forbidden):
            raise EcosystemError('Coursework still contains obsolete ecosystem entries')
        journal = dict(schema_version=1, status='complete', coursework=str(destination),
                       ecosystem=str(root), completed_at=now(), runtime=repaired,
                       coursework_is_real_directory=True, cross_repository_links=False)
        atomic_json(root / 'ecosystem/data/layout.json', journal)
        return journal
