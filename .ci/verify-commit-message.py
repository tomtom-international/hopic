#!/usr/bin/env python3

# Copyright (c) 2019 - 2020 TomTom N.V. (https://tomtom.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This is a simplistic implementation of checking adherance to Conventional Commits https://www.conventionalcommits.org/

from collections import namedtuple
from functools import wraps
from inspect import getfullargspec
import difflib
import itertools
import os
import re
import sys
import subprocess
from typing import (
        get_type_hints,
        Iterable,
        Sequence,
    )
try:
    from stemming.porter2 import stem
except ImportError:
    stem = None
try:
    import regex
except ImportError:
    regex = None

def type_check(f):
    @wraps(f)
    def validate_parameters(*args, **kwargs):
        func_args = getfullargspec(f)[0]
        kw = kwargs.copy()
        kw.update(dict(zip(func_args, args)))

        for attr_name, attr_type in get_type_hints(f).items():
            if attr_name == 'return':
                continue

            if attr_name in kw and not isinstance(kw[attr_name], attr_type):
                raise TypeError('Argument {!r} is not of type {}'.format(attr_name, attr_type))
        return f(*args, **kwargs)
    return validate_parameters


MatchGroup = namedtuple('MatchGroup', ('text', 'name', 'start', 'end'))


commit = 'HEAD'
if len(sys.argv) >= 2:
    commit = sys.argv[1]

if os.path.isfile(commit) and not re.match(r'^[0-9a-fA-F]{40}$', commit):
    with open(commit, encoding='UTF-8') as f:
        message = f.read()
        try:
            comment_char = subprocess.check_output(('git', 'config', '-z', 'core.commentChar'))[:-1].decode('UTF-8')
        except:
            comment_char = '#'
        cut_line = message.find(comment_char + ' ------------------------ >8 ------------------------\n')
        if cut_line >= 0 and (cut_line == 0 or message[cut_line - 1] == '\n'):
            message = message[:cut_line]
        # Strip comments
        message = re.sub(r'^#[^\n]*\n?', '', message, flags=re.MULTILINE)
        # Strip trailing whitespace from lines
        message = re.sub(r'[ \t]+$', '', message, flags=re.MULTILINE)
        # Merge consecutive empty lines into a single empty line
        message = re.sub(r'(?<=\n\n)\n+', '', message)
        # Remove empty lines from the beginning and end
        while message[:1] == '\n':
            message = message[1:]
        while message[-2:] == '\n\n':
            message = message[:-1]
else:
    message = subprocess.check_output(('git', 'show', '-q', '--format=%B', commit, '--'))[:-1].decode('UTF-8')
lines = message.splitlines()

# Split the message body in paragraphs
body = []
for n, line in enumerate(lines[2:], 2):
    if line:
        if not body:
            body.append([n, ''])
        body[-1][1] += ('\n' if body[-1][1] else '') + line
    elif body[-1][1]:
        body.append([n, ''])

errors = []

if body and not body[-1][1]:
    errors.append("\x1B[1m{commit}:{}:1: \x1B[31merror\x1B[39m: commit message body is followed by empty lines\x1B[m".format(len(lines), commit=commit))

autosquash = re.match(r'^(fixup|squash)!\s+', lines[0])
subject_start = autosquash.end() if autosquash is not None else 0

merge = re.match(r'^Merge.*?(?:$|:\s+)', lines[0][subject_start:])
subject_start += merge.end() if merge is not None else 0
if subject_start == len(lines[0]) and re.match(r"^Merge branch '.*?'(?:into '.*')?$", lines[0]):
    # Ignore branch merges
    sys.exit(0)

subject_re = re.compile(r'''
    ^
    # 1. Commits MUST be prefixed with a type, which consists of a noun, feat, fix, etc., ...
    (?P<type_tag>\w+)

    # 4. An optional scope MAY be provided after a type. A scope is a phrase describing a section of the codebase
    #    enclosed in parenthesis, e.g., fix(parser):
    (?: \( (?P<scope> [^()]* ) \) )?

    # 1. Commits MUST be prefixed with a type, ..., followed by a colon and a space.
    (?P<separator>:?[ ]?)

    # 5. A description MUST immediately follow the type/scope prefix. The description is a short description of the
    #    code changes, e.g., fix: array parsing issue when multiple spaces were contained in string.
    (?P<description>.*)

    $
    ''', re.VERBOSE)
subject = subject_re.match(lines[0][subject_start:])
if not subject:
    error = "\x1B[1m{commit}:1:1: \x1B[31merror\x1B[39m: commit message's subject not formatted according to Conventional Commits\x1B[m\n{subject_re.pattern}\n".format(**locals())
    error += lines[0] + '\n'
    error += ' ' * subject_start + '\x1B[32m' + '^' * max(len(lines[0]) - subject_start, 1) + '\x1B[39m'
    errors.append(error)

def extract_match_group(match, group, start=0):
    if match is None or match.group(group) is None:
        return None
    return MatchGroup(name=group, text=match.group(group), start=match.start(group)+start, end=match.end(group)+start)
type_tag    = extract_match_group(subject, 'type_tag'   , subject_start)
scope       = extract_match_group(subject, 'scope'      , subject_start)
separator   = extract_match_group(subject, 'separator'  , subject_start)
description = extract_match_group(subject, 'description', subject_start)

@type_check
def complain_about_excess_space(match: MatchGroup, line: int = 0) -> None:
    excess_whitespace = list(itertools.chain.from_iterable(
            range(*space.span()) for space in re.finditer(r'\s{2,}|^\s+|\s+$', match.text)))
    if excess_whitespace:
        error = "\x1B[1m{commit}:{line}:{col}: \x1B[31merror\x1B[39m: excess whitespace in {match.name}\x1B[m\n".format(line=line + 1, col=match.start + 1 + excess_whitespace[0], match=match, commit=commit)
        error += lines[0] + '\n'
        error += '\x1B[32m'
        cur = -match.start
        for pos in excess_whitespace:
            error += ' ' * (pos - cur) + '^'
            cur = pos + 1
        error += '\x1B[39m'
        errors.append(error)

accepted_tags = (
        'build',
        'chore',
        'ci',
        'docs',
        'perf',
        'refactor',
        'revert',
        'style',
        'test',
        'improvement',
    )

# 1. Commits MUST be prefixed with a type, which consists of a noun, feat, fix, etc., followed by a colon and a space.
# 2. The type feat MUST be used when a commit adds a new feature to your application or library.
# 3. The type fix MUST be used when a commit represents a bug fix for your application.
if type_tag and type_tag.text not in accepted_tags and type_tag.text not in ('feat', 'fix'):
    error = "\x1B[1m{commit}:1:1: \x1B[31merror\x1B[39m: use of type tag that's neither 'feat', 'fix' nor whitelisted ({})\x1B[m\n".format(', '.join(accepted_tags), **locals())
    error += lines[0] + '\n'
    error += ' ' * type_tag.start + '\x1B[31m' + '~' * (type_tag.end - type_tag.start) + '\x1B[39m'
    possibilities = difflib.get_close_matches(type_tag.text, ('feat', 'fix') + accepted_tags, n=1)
    if possibilities:
        error += '\n' + possibilities[0]
    errors.append(error)

# 4. An optional scope MAY be provided after a type. A scope is a phrase describing a section of the codebase enclosed
#    in parenthesis, e.g., fix(parser):
if scope is not None:
    complain_about_excess_space(scope)

# 1. Commits MUST be prefixed with a type, ..., followed by a colon and a space.
if separator and separator.text != ': ':
    error = "\x1B[1m{commit}:1:{}: \x1B[31merror\x1B[39m: commit message's subject lacks a ': ' separator after the type tag\x1B[m\n".format(separator.start + 1, **locals())
    error += lines[0] + '\n'
    error += separator.start * ' ' + '\x1B[32m^' * max(1, separator.end - separator.start) + '\x1B[39m'
    errors.append(error)

# 5. A description MUST immediately follow the type/scope prefix. The description is a short description of the
#    code changes, e.g., fix: array parsing issue when multiple spaces were contained in string.
if description and not description.text:
    error = "\x1B[1m{commit}:1:{}: \x1B[31merror\x1B[39m: commit message's subject lacks a description after the type tag\x1B[m\n".format(description.start + 1, **locals())
    error += lines[0] + '\n'
    error += ' ' * description.start + '\x1B[32m^\x1B[39m'
    errors.append(error)

if description is not None:
    complain_about_excess_space(description)

    # Prevent upper casing the first letter of the first word, this is not a book-style sentence.
    if regex is None:
        title_case_re = re.compile(r'\b[A-Z][a-z]*(?:\s+[A-Z][a-z]*)*\b')
    else:
        title_case_re = regex.compile(r'\b[\p{Lu}\p{Lt}]\p{Ll}*(?:\s+[\p{Lu}\p{Lt}]\p{Ll}*)*\b')
    title_case_word = extract_match_group(title_case_re.match(description.text), 0, description.start)
    if title_case_word:
        error = "\x1B[1m{commit}:1:{}: \x1B[31merror\x1B[39m: don't use title case in the description\x1B[m\n".format(title_case_word.start + 1, **locals())
        error += lines[0] + '\n'
        error += ' ' * title_case_word.start + '\x1B[32m' + '^' + '~' * (title_case_word.end - title_case_word.start - 1) + '\x1B[39m'
        errors.append(error)

    # Disallow referring to review comments because it's a poor excuse for a proper commit message
    review_comment_ref = None
    if stem is not None:
        description_words = tuple((word.group(), stem(word.group()).lower(), word.start(), word.end()) for word in re.finditer(r'\b(?:\w|[-])+\b', description.text))
        opt_prefix = frozenset({
                'all',
                'as',
                'code',
                'edit',
                'for',
                'minor',
                'per',
                'request',
            })
        opt_suffix = frozenset({
                'comment',
                'find',
                'with',
            })
        reference_words = frozenset({
                'accord',
                'bitbucket',
                'address',
                'appli',
                'chang',
                'fix',
                'implement',
                'incorpor',
                'per',
                'process',
                'resolv',
                'rework',
            })
        ref_start, ref_end = None, None
        def encounter(word: str, opts: Sequence[str]) -> bool:
            return bool(word in opts or difflib.get_close_matches(word, opts, cutoff=0.9))
        for idx, (word, stemmed, start, end) in enumerate(description_words):
            if stemmed not in ('review', 'onlin'):
                continue
            min_idx = idx
            while min_idx > 0 and encounter(description_words[min_idx-1][1], opt_prefix):
                min_idx -= 1
            if min_idx > 0 and encounter(description_words[min_idx-1][1], reference_words):
                min_idx -= 1
                ref_start = description_words[min_idx][2]
                ref_end = end
            max_idx = idx
            while max_idx + 1 < len(description_words) and encounter(description_words[max_idx+1][1], opt_suffix):
                max_idx += 1
                if ref_end is not None:
                    ref_end = max(ref_end, description_words[max_idx][3])
            if max_idx + 1 < len(description_words) and encounter(description_words[max_idx+1][1], reference_words):
                max_idx += 1
                if ref_start is None:
                    ref_start = len(description.text)
                if ref_end is None:
                    ref_end = 0
                ref_start = min(ref_start, description_words[min_idx][2])
                ref_end = max(ref_end, description_words[max_idx][3])
            if ref_start is None and ref_end is None:
                brace_prefix = re.match(r'^.*([(])\s*', description.text[:start])
                brace_suffix = re.match(r'\s*([)])', description.text[description_words[max_idx][3]:])
                if brace_prefix and brace_suffix:
                    ref_start = brace_prefix.start(1)
                    ref_end = brace_prefix.end(1)
            if ref_start is None and ref_end is None:
                for try_idx, (try_word, try_stemmed, _, _) in enumerate(description_words[min_idx:max_idx+1], min_idx):
                    if encounter(try_stemmed, reference_words):
                        ref_start = description_words[min_idx][2]
                        ref_end = description_words[max_idx][3]
                        break
            if ref_start is not None and ref_end is not None:
                review_comment_ref = MatchGroup(name=0, text=description.text[ref_start:ref_end], start=ref_start+description.start, end=ref_end+description.start)
                break
    else:
        review_comment_ref = re.search(r'''
            (?:accord|address|apply|implement|incorporat|process|resolv|rework)(?:e|e?[sd]|ing)?\s+
            (?:all\s+)?
            (?:minor\s+)?
            (?:code\s+)?
            review(?:\s+(?:comment|finding)s?)?\b
            |review\s+comments\s+    (?:accord|address|apply|implement|incorporat|process|resolv|rework)(?:e|e?[sd]|ing)?\s+
            |\breview\s+rework(?:ing|ed)?\b
            |[(] \s* review \s+ (?:comment|finding)s? \s* [)]
            ''',
            description.text, re.VERBOSE|re.IGNORECASE)
        review_comment_ref = extract_match_group(review_comment_ref, 0, description.start)

    if review_comment_ref:
        start = review_comment_ref.start
        error = "\x1B[1m{commit}:1:{}: \x1B[31merror\x1B[39m: add context directly to commit messages instead of referring to review comments\x1B[m\n".format(start + 1, **locals())
        error += lines[0] + '\n'
        error += ' ' * start + '\x1B[32m' + '^' * (review_comment_ref.end - review_comment_ref.start) + '\x1B[39m\n'
        error += "\x1B[1m{commit}:1:{}: \x1B[30mnote\x1B[39m: prefer using --fixup when fixing previous commits in the same pull request\x1B[m".format(start + 1, **locals())
        errors.append(error)

    # Our own requirements on the description
    # No JIRA tickets in the subject line, because it wastes precious screen estate (80 chars)
    non_jira_projects = (
            'AES', # AES-128
            'PEP', # PEP-440
            'SHA', # SHA-256
            'UTF', # UTF-8
            'VT',  # VT-220
        )
    jira_re = re.compile(r'\b(?!' + '|'.join(re.escape(i + '-') for i in non_jira_projects) + r')[A-Z]+-[0-9]+\b')
    jira_tickets = []
    for m in jira_re.finditer(description.text):
        jira_tickets.extend(range(*m.span()))
    if jira_tickets:
        error = "\x1B[1m{commit}:{line}:{col}: \x1B[31merror\x1B[39m: commit message's subject contains Jira tickets\x1B[m\n".format(line=1, col=description.start + 1 + jira_tickets[0], commit=commit)
        error += lines[0] + '\n'
        error += '\x1B[32m'
        cur = -description.start
        for pos in jira_tickets:
            error += ' ' * (pos - cur) + '^'
            cur = pos + 1
        error += '\x1B[39m'
        errors.append(error)

    # Disallow ending the description with punctuation
    if re.match(r'.*[.!?,]$', description.text):
        error = "\x1B[1m{commit}:1:{}: \x1B[31merror\x1B[39m: commit message's subject ends with punctuation\x1B[m\n".format(len(lines[0]), **locals())
        error += lines[0] + '\n'
        error += ' ' * (len(lines[0]) - 1) + '\x1B[32m^\x1B[39m'
        errors.append(error)

    blacklist_start_words = (
            'added',
            'adds',
            'adding'
            'applied',
            'applies',
            'applying',
            'edited',
            'edits',
            'editing',
            'expanded',
            'expands',
            'expanding',
            'fixed',
            'fixes',
            'fixing',
            'removed',
            'removes',
            'removing',
            'renamed',
            'renames',
            'renaming',
            'deleted',
            'deletes',
            'deleting',
            'updated',
            'updates',
            'updating',
            'ensured',
            'ensures',
            'ensuring',
            'resolved',
            'resolves',
            'resolving',
            'verified',
            'verifies',
            'verifying',

            # repeating the tag is frowned upon as well
            type_tag.text,
        )
    blacklisted = extract_match_group(re.match(r'^(?:' + '|'.join(re.escape(w) for w in blacklist_start_words) + r')\b', description.text, flags=re.IGNORECASE), 0, description.start)
    if blacklisted:
        error = "\x1B[1m{commit}:1:{}: \x1B[31merror\x1B[39m: commit message's description contains blacklisted word or repeats type tag\x1B[m\n".format(blacklisted.start + 1, **locals())
        error += lines[0] + '\n'
        error += blacklisted.start * ' ' + '\x1B[32m^' * (blacklisted.end - blacklisted.start) + '\x1B[39m\n'
        error += "\x1B[1m{commit}:1:{}: \x1B[30mnote\x1B[39m: prefer using the imperative for verbs\x1B[m".format(blacklisted.start + 1, **locals())
        errors.append(error)

if len(lines[0]) > 80:
    error = "\x1B[1m{commit}:1:81: \x1B[31merror\x1B[39m: commit message's subject exceeds line length of 80 by {} characters\x1B[m\n".format(len(lines[0]) - 80, **locals())
    error += lines[0] + '\n'
    error += ' ' * 79 + '\x1B[32m^' + '~' * (len(lines[0]) - 80) + '\x1B[39m'
    errors.append(error)

if len(lines) > 1 and lines[1]:
    error = "\x1B[1m{commit}:2:1: \x1B[31merror\x1B[39m: commit message subject and body are not separated by an empty line\x1B[m\n".format(**locals())
    error += lines[1] + '\n'
    error += '\x1B[31m' + '~' * len(lines[1]) + '\x1B[39m'
    errors.append(error)

# 8. Breaking changes MUST be indicated at the very beginning of the footer or body section of a commit. A breaking
#    change MUST consist of the uppercase text BREAKING CHANGE, followed by a colon and a space.
for lineno, paragraph in body:
    for m in re.finditer(r'\bBREAKING(\s+)CHANGE\b(\s*)(:?)(\s*)(\S?)', paragraph):
        if m.start() == 0:
            if paragraph[:m.end(4)] != 'BREAKING CHANGE: ' or not m.group(m.lastindex):
                line_end = paragraph.find('\n')
                error = "\x1B[1m{commit}:{line}:1: \x1B[31merror\x1B[39m: breaking changes should start with _exactly_ 'BREAKING CHANGE: ' and be followed by text immediately\x1B[m\n".format(line=lineno + 1, commit=commit)
                error += paragraph[:line_end] + '\n'
                error += '\x1B[32m'
                cur = 0
                for group, expect in zip(range(1, m.lastindex + 1), (' ', '', ':', ' ')):
                    if m.group(group) != expect:
                        error += ' ' * (m.start(group) - cur)
                        error += '^' * len(m.group(group))
                        cur = m.end(group)
                # 9. A description MUST be provided after the BREAKING CHANGE:, describing what has changed about the
                #    API, e.g., BREAKING CHANGE: environment variables now take precedence over config files.
                if not m.group(m.lastindex):
                    error += ' ' * (m.start(m.lastindex) - cur)
                    error += '^'
                    cur = m.start(m.lastindex) + 1
                error += '\x1B[39m'
                errors.append(error)
        else:
            line_start = paragraph.rfind('\n', 0, m.start()) + 1
            line_end = paragraph.find('\n', m.start(2))
            line = lineno + paragraph[:line_start + 1].count('\n')
            start = m.start() - line_start
            error = "\x1B[1m{commit}:{line}:{col}: \x1B[31merror\x1B[39m: body contains 'BREAKING CHANGE' at other location than start of paragraph\x1B[m\n".format(line=line + 1, col=start + 1, commit=commit)
            error += paragraph[line_start:line_end] + '\n'
            error += '\x1B[32m' + ' ' * start
            error += ''.join('\n' if c == '\n' else '^' for c in paragraph[m.start():m.start(2)])
            error += '\x1B[39m'
            errors.append(error)

for error in errors:
    print(error, file=sys.stderr)
if errors:
    sys.exit(1)
