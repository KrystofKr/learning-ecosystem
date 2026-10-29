#!/usr/bin/env python3
"""Read the current Justin Sung synthesis with document hashes and line citations."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys


PROJECT = Path(__file__).resolve().parents[3]


def default_synthesis():
    pointer = PROJECT / 'ecosystem/data/methods/current.json'
    if pointer.is_file():
        current = json.loads(pointer.read_text())
        version = current.get('version', '')
        if not isinstance(version, str) or not re.fullmatch(r'[a-f0-9]{64}', version):
            raise MethodError('Invalid published method version; run ecosystem.py sync.')
        return PROJECT / 'ecosystem/data/methods/versions' / version / 'synthesis.txt'
    return PROJECT / 'knowledge_pipeline/data/justin_sung/synthesis/main.txt'


DEFAULT_SYNTHESIS = PROJECT / 'knowledge_pipeline/data/justin_sung/synthesis/main.txt'


class MethodError(ValueError):
    pass


class MethodLibrary:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        data = self.path.read_bytes()
        if self.path.read_bytes() != data:
            raise MethodError('Synthesis changed while reading; retry with the completed version.')
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.lines = data.decode('utf-8-sig').splitlines()
        headings = []
        for index, line in enumerate(self.lines[:-1]):
            match = re.fullmatch(r'([1-9][0-9]?)\. (.+)', line)
            following = self.lines[index + 1].strip()
            if match and len(following) >= 8 and set(following) == {'='}:
                headings.append((int(match[1]), match[2], index + 1))
        numbers = [item[0] for item in headings]
        if not numbers or len(set(numbers)) != len(numbers):
            raise MethodError('Missing or ambiguous numbered synthesis chapters.')
        self.chapters = [
            dict(chapter=number, title=title, line_start=start,
                 line_end=headings[index + 1][2] - 1 if index + 1 < len(headings) else len(self.lines))
            for index, (number, title, start) in enumerate(headings)
        ]

    def metadata(self):
        return dict(path=str(self.path), document_sha256=self.sha256)

    def catalog(self):
        return dict(**self.metadata(), chapters=self.chapters)

    def bounds(self, chapter=None, section=None):
        start, end = 1, len(self.lines)
        if chapter is not None:
            item = next((item for item in self.chapters if item['chapter'] == chapter), None)
            if item is None:
                raise MethodError('Unknown chapter.')
            start, end = item['line_start'], item['line_end']
        if section is not None:
            if not re.fullmatch(r'[1-9][0-9]?\.[1-9][0-9]?', section):
                raise MethodError('Section must use a number such as 4.2.')
            parent = int(section.split('.')[0])
            if chapter is not None and parent != chapter:
                raise MethodError('Section belongs to another chapter.')
            start, end = self.bounds(parent)
            found = next((i for i in range(start, end + 1)
                          if self.lines[i - 1].startswith(section + ' ')), None)
            if found is None:
                raise MethodError('Unknown section.')
            start = found
            for i in range(start + 1, end + 1):
                if re.match(r'^[1-9][0-9]?\.[1-9][0-9]? ', self.lines[i - 1]):
                    end = i - 1
                    break
        return start, end

    def read(self, chapter=None, section=None):
        start, end = self.bounds(chapter, section)
        return dict(**self.metadata(), chapter=chapter, section=section,
                    line_start=start, line_end=end,
                    lines=[dict(line=i, text=self.lines[i - 1]) for i in range(start, end + 1)])

    def search(self, query, chapter=None, start=0, limit=5):
        if not query.strip():
            raise MethodError('Query cannot be empty.')
        lower, upper = self.bounds(chapter)
        matches = [i for i in range(lower, upper + 1) if query.casefold() in self.lines[i - 1].casefold()]
        snippets = []
        for i in matches[start:start + limit]:
            first, last = max(lower, i - 3), min(upper, i + 5)
            snippets.append(dict(line_start=first, line_end=last,
                                 lines=[dict(line=n, text=self.lines[n - 1]) for n in range(first, last + 1)]))
        return dict(**self.metadata(), total=len(matches), start=start, matches=snippets)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthesis', default=os.environ.get('JUSTIN_SUNG_SYNTHESIS'))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('catalog')
    read = commands.add_parser('read')
    read.add_argument('--chapter', type=int)
    read.add_argument('--section')
    search = commands.add_parser('search')
    search.add_argument('--query', required=True)
    search.add_argument('--chapter', type=int)
    search.add_argument('--start', type=int, default=0)
    search.add_argument('--limit', type=int, default=5)
    args = parser.parse_args(argv)
    if args.command == 'search' and (args.start < 0 or not 1 <= args.limit <= 50):
        parser.error('Use a nonnegative --start and --limit between 1 and 50.')
    if args.command == 'read' and args.chapter is None and args.section is None:
        parser.error('read requires --chapter or --section to avoid loading the whole library.')
    try:
        library = MethodLibrary(args.synthesis or default_synthesis())
        if args.command == 'catalog':
            result = library.catalog()
        elif args.command == 'read':
            result = library.read(args.chapter, args.section)
        else:
            result = library.search(args.query, args.chapter, args.start, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, MethodError) as exc:
        print(f'Method library unavailable: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
