#!/usr/bin/env python3
"""
vcsgrep: a tool for quick grepping of Hg and Git repos.

For example, "hgg FOO .cpp" translates to grepping for FOO in all .cpp
files tracked by Mercurial.

Requires Python 3, GNU grep, GNU sed and GNU xargs.

Install by symlinking vcsgrep.py as "hgg" and/or "ggit" in your ~/bin
or similar, then run "hgg --help" / "ggit --help".
"""

import fcntl
import os
import re
import shlex
import subprocess
import sys


version = '0.9'
editor_flags = ['--vim', '--gvim']
editors_that_need_stdin = ['vim']

# default to exended regex matching instead of basic (-G).
grep_default_matcher = '-E'
# these matcher selection flags override the default
grep_flags_matcher_select = 'EFGP'
# if -e or -f is used, there's no implicit ("unflagged") pattern argument
grep_flags_suppress_implicit_pattern_arg = 'ef'
# these grep flags are followed by an argument
grep_flags_with_argument = 'efmABCdD'


def glob_to_grep_pattern(glob):
    r""" Performs an approximate translation from a (Mercurial-like)
        extended glob pattern into a regex suitable for grepping paths.

        >>> glob_to_grep_pattern('')
        ''
        >>> glob_to_grep_pattern('img????.cpp')
        '^img....\\.cpp(/|$)'
        >>> glob_to_grep_pattern('*.cpp')
        '^[^/]*\\.cpp(/|$)'
        >>> glob_to_grep_pattern('main.[ch]')
        '^main\\.[ch](/|$)'
        >>> glob_to_grep_pattern('*.{c,cpp,h}')
        '^[^/]*\\.(c|cpp|h)(/|$)'
        >>> glob_to_grep_pattern('**/*.[ch]')
        '^.*[^/]*\\.[ch](/|$)'

        >>> glob_to_grep_pattern('.')
        ''
        >>> glob_to_grep_pattern('./foo/./bar')
        '^foo\\/bar(/|$)'
        >>> glob_to_grep_pattern('../foo/bar**')
        '^\\.\\.\\/foo\\/bar.*(/|$)'

        Mercurial supports nested globbing inside braces, too:
        >>> glob_to_grep_pattern('main.{[ch],[ch]pp,*zzz}')
        '^main\\.([ch]|[ch]pp|[^/]*zzz)(/|$)'
        >>> glob_to_grep_pattern('{foo,ba{r,z}}')
        '^(foo|ba(r|z))(/|$)'

        Invalid patterns:

        >>> glob_to_grep_pattern('foo{bar')
        Traceback (most recent call last):
        ...
        ValueError: invalid glob pattern (unclosed "}"): foo{bar

        >>> glob_to_grep_pattern('foo{bar}}')
        Traceback (most recent call last):
        ...
        ValueError: invalid glob pattern (unexpected "}"): foo{bar}}
    """
    glob2 = re.sub(r'(^|(?<=/))\.(/|$)', '', glob)
    if not glob2:
        return ''

    brace_depth = 0
    result = ['^']
    for i, piece in enumerate(re.split(r'(\*\*/?|[*?,{}]|\[[^]]+\])', glob2)):
        if not piece:
            continue
        if i % 2 == 0:
            result.append(re.escape(piece))
        elif piece == '?':
            result.append('.')
        elif piece == '*':
            result.append('[^/]*')
        elif piece[0] == '*': # '**' or '**/'
            result.append('.*')
        elif piece[0] == '[':
            result.append(piece)
        elif piece == '{':
            result.append('(')
            brace_depth += 1
        elif piece == ',':
            if brace_depth == 0:
                result.append(',')
            else:
                result.append('|')
        elif piece == '}':
            if brace_depth == 0:
                raise ValueError('invalid glob pattern (unexpected "}"): %s' % glob)
            result.append(')')
            brace_depth -= 1
        else:
            assert False, piece

    if brace_depth > 0:
        raise ValueError('invalid glob pattern (unclosed "}"): %s' % glob)

    result.append('(/|$)')
    return ''.join(result)


class ArgParser:
    """ Parses argument list and determines which args are for hgg itself,
        which are for hg file, and which are for grep.

        >>> ArgParser('hello')
        grep_args: ['-E', 'hello']
        grep_color_arg: '--color'

        >>> ArgParser('hello', '--color')
        grep_args: ['-E', 'hello']
        grep_color_arg: '--color'

        >>> ArgParser('hello', '--color=always')
        grep_args: ['-E', 'hello']
        grep_color_arg: '--color=always'

        >>> ArgParser('hello', '-G')
        grep_args: ['hello', '-G']
        grep_color_arg: '--color'

        >>> ArgParser('--show', 'hello', '--gvim')
        editor: 'gvim'
        grep_args: ['-E', 'hello']
        grep_color_arg: '--color'
        show: True

        >>> ArgParser('pat', '.h', '.cpp')
        grep_args: ['-E', 'pat']
        grep_color_arg: '--color'
        include_globs: ['**{.h,.cpp}']

        >>> ArgParser('pat', '.cpp', 'src', 'docs/')
        grep_args: ['-E', 'pat']
        grep_color_arg: '--color'
        include_globs: ['src/**{.cpp}', 'docs/**{.cpp}']

        >>> ArgParser('.pattern', 'glob1', '.extglob', '*.glob2', '*/glob3')
        grep_args: ['-E', '.pattern']
        grep_color_arg: '--color'
        include_globs: ['glob1/**{.extglob}', '*.glob2/**{.extglob}', '*/glob3/**{.extglob}']

        >>> ArgParser('-Ffpattern-file')
        grep_args: ['-Ffpattern-file']
        grep_color_arg: '--color'

        >>> ArgParser('-Ff', 'pattern-file')
        grep_args: ['-Ff', 'pattern-file']
        grep_color_arg: '--color'

        >>> ArgParser('-e', 'pat1', '-e', 'pat2', 'path')
        grep_args: ['-E', '-e', 'pat1', '-e', 'pat2']
        grep_color_arg: '--color'
        include_globs: ['path']

        >>> ArgParser('-r', 'ae279a85a0ad', 'hello')
        grep_args: ['-E', 'hello']
        grep_color_arg: '--color'
        revisions: ['ae279a85a0ad']

        >>> ArgParser('--rev', 'ae279a85a0ad', '-r', '.', 'hello')
        grep_args: ['-E', 'hello']
        grep_color_arg: '--color'
        revisions: ['ae279a85a0ad', '.']

        >>> ArgParser('-rae279a85a0ad', '-r.', 'hello')
        grep_args: ['-E', 'hello']
        grep_color_arg: '--color'
        revisions: ['ae279a85a0ad', '.']

        For -V and -h flags, the flag is still put in grep_args, but
        it doesn't matter as the version/help handling kicks in first.
        >>> ArgParser('hello', '-GV')
        grep_args: ['hello', '-GV']
        grep_color_arg: '--color'
        version: True
    """

    def __init__(self, *args):
        self.explain = False
        self.help = False
        self.show = False
        self.version = False

        self.include_globs = []
        self.exclude_globs = []
        self.grep_args = []
        self.editor = None
        self.grep_color_arg = '--color'
        self.revisions = []

        extension_globs = []
        use_default_matcher = True
        next_arg_processor = None
        awaiting_implicit_pattern_arg = True

        for arg in args:
            if next_arg_processor is not None:
                next_arg_processor(arg)
                next_arg_processor = None
            elif arg in editor_flags:
                self.editor = arg.lstrip('-')
            elif arg in ('--explain', '--help', '--show', '--version'):
                setattr(self, arg[2:], True)
            elif arg == '-X':
                next_arg_processor = self.exclude_globs.append
            elif arg == '--color' or arg.startswith('--color='):
                self.grep_color_arg = arg
            elif arg == '--rev' or arg.startswith('-r'):
                if arg in ('--rev', '-r'):
                    next_arg_processor = self.revisions.append
                else:
                    self.revisions.append(arg[2:])
            elif len(arg) >= 2 and arg[0] == '-':
                self.grep_args.append(arg)

                # determine if there's a follow-up argument
                if arg[1] != '-':
                    for i in range(1, len(arg)):
                        if arg[i] == 'V':
                            self.version = True
                            continue
                        if arg[i] in grep_flags_suppress_implicit_pattern_arg:
                            awaiting_implicit_pattern_arg = False
                        elif arg[i] in grep_flags_matcher_select:
                            use_default_matcher = False

                        if arg[i] in grep_flags_with_argument:
                            # see if flag argument is separate (-C 3) or "embedded" (-C3)
                            if i == len(arg) - 1:
                                # next arg is for grep
                                next_arg_processor = self.grep_args.append
                            break

            elif awaiting_implicit_pattern_arg:
                awaiting_implicit_pattern_arg = False
                self.grep_args.append(arg)
            elif arg.startswith('.') and '/' not in arg:
                extension_globs.append(arg)
            else:
                self.include_globs.append(arg)

        if extension_globs:
            ext = '**{%s}' % ','.join(extension_globs)
            if self.include_globs:
                self.include_globs = [ glob.rstrip('/') + '/' + ext for glob in self.include_globs ]
            else:
                self.include_globs = [ ext ]

        if use_default_matcher:
            self.grep_args.insert(0, grep_default_matcher)

    def __repr__(self):
        return '\n'.join(
            '%s: %r' % (k, v)
            for k, v in sorted(self.__dict__.items())
            if k[0] != '_' and v
        )


def globs_to_grep_pipe(prog, patterns):
    try:
        # (TODO: grep -z -Z is not available on OS X)
        return '| grep -EzZ %s' % quote(map(glob_to_grep_pattern, patterns), pattern='-e %s')
    except ValueError as e:
        raise SystemExit('%s: %s' % (prog, e))


if __name__ == '__main__':
    if sys.argv[0].endswith('hgg'):
        prog = 'hgg'
    elif sys.argv[0].endswith('ggit') or re.search(r'\bggit\b', sys.argv[0]):
        prog = 'ggit'
    else:
        raise SystemExit('%s: must be invoked as "hgg" or "gitg"' % sys.argv[0])

    args = ArgParser(*sys.argv[1:])

    if args.explain:
        print(args)
        sys.exit(0)

    if args.version:
        print('vcsgrep %s' % version)
        for util in 'grep', 'sed', 'xargs', 'hg', 'git':
            try:
                output = subprocess.check_output(util + ' --version', shell=True, stderr=subprocess.STDOUT)
                print(output.decode('utf-8', 'replace').splitlines()[0])
            except subprocess.CalledProcessError:
                print('%s: not found or broken (or BSD/macOS version?)' % util)
        sys.exit(0)

    if args.help or len(sys.argv) < 2 or sys.argv[1:] == ['-h']:
        sys.stderr.write('''\
usage: {prog} [--show] [{editor_flags_pipe}] [GREP-OPTIONS] GREP-PATTERN... [FILE-PATTERN]...
Searches for GREP-PATTERN across tracked files (filtered by FILE-PATTERNs,
if any) in the {vcs} working directory. Skips binary files and symlinks,
and defaults to "grep -E" (extended regexp) mode.

Example: {prog} -i "hello" .h .cpp src/

For help on GREP-OPTIONS and GREP-PATTERN, see "man grep". FILE-PATTERNS
use extended glob syntax (** and {{}} supported), or plain file extensions.
Plain file extensions (e.g. '.py') limit permitted file extensions; using
these turn all other file patterns into directory patterns (and implicitly
adds e.g. '/**.py').

Use --show to see the grep command instead of executing it. Use one of
{editor_flags_comma} to open matching files in editor. Use --explain
to explain how exactly the {prog} arguments were parsed.
'''.format(
            prog=prog,
            editor_flags_pipe='|'.join(editor_flags),
            editor_flags_comma=', '.join(editor_flags),
            vcs='Mercurial' if prog == 'hgg' else 'Git',
        ))
        if prog == 'hgg':
            sys.stderr.write('''
Use -r REV to grep only files changed since REV, or -r R1 -r R2 to grep
only files changed between revisions R1 and R2.
''')
        sys.exit(1)

    grep = 'grep --binary-files=without-match -H'

    def quote(args, pattern='%s'):
        return ' '.join(pattern % shlex.quote(a) for a in args)

    if prog == 'hgg':
        if args.revisions:
            # grep only files changed since / between given revision(s)
            cmd = "HGPLAIN=1 hg status --print0 --no-status -X 'set:symlink()' %s %s %s" % (
                quote(args.include_globs, pattern='-I %s'),
                quote(args.exclude_globs, pattern='-X %s'),
                quote(args.revisions, '--rev %s'),
            )
        else:
            cmd = 'HGPLAIN=1 hg files --print0 %s %s' % (
                quote(args.include_globs, pattern='-I %s'),
                quote(args.exclude_globs, pattern='-X %s'),
            )
            # filter out symlinks
            # (TODO: sed --null-data is not available on OS X)
            # Could use -X 'set:symlink()', but filesets are surprisingly slow in
            # Mercurial 4.2.2. Listing 50k files in a repo is 2.5s if using file
            # sets vs. 1.0s for the sed solution, quite a lot when the actual
            # grepping is only 0.6s.
            cmd += ' --verbose | sed --null-data -n -e %s' % shlex.quote(r's/^.........[0-9] [^l] \(.*\)/\1/p')
    elif prog == 'ggit':
        if args.revisions:
            raise SystemExit('%s: --rev is not implemented for Git' % prog)

        # filter out symlinks, submodules
        # (TODO: sed --null-data is not available on OS X)
        cmd = 'git ls-files --stage -z | sed --null-data -n -e %s' % shlex.quote(r's/^100... .*\t\(.*\)/\1/p')
        if args.include_globs:
            cmd += globs_to_grep_pipe(prog, args.include_globs)
        if args.exclude_globs:
            cmd += globs_to_grep_pipe(prog, args.exclude_globs) + ' -v' # invert match
    else:
        assert False, prog

    if args.editor:
        args.grep_args.extend(['-l', '--null'])
    else:
        args.grep_args.extend([args.grep_color_arg])
    cmd += ' | xargs -0 %s %s --' % (grep, quote(args.grep_args))

    if args.editor:
        if args.editor in editors_that_need_stdin and not args.show:
            # This editor needs stdin, so we need to use another FD than stdin with xargs.
            # Unless we're showing the command, because we can't show this workaround as a command.

            def popen(shell_cmd, fds={}, **kwargs):
                # Python 2.7:
                if any(fds.values()):
                    # if there are any fds we MUST pass, explicitly close the rest on exec
                    kwargs['close_fds'] = False
                    for fd, pass_fd in fds.items():
                        fcntl.fcntl(fd, fcntl.F_SETFD, 0 if pass_fd else fcntl.FD_CLOEXEC)
                else:
                    # if there are no fds we MUST pass, just close them all on exec
                    kwargs['close_fds'] = True

                # Py3.2: pass_fds=[r] optional but better
                return subprocess.Popen(shell_cmd, shell=True, **kwargs)

            r, w = os.pipe()
            p_grep = popen(cmd, fds={r: False, w: False}, stdout=w)
            p_vim = popen(
                'xargs --arg-file=/dev/fd/%d --no-run-if-empty -0 %s' % (r, args.editor),
                fds={r: True, w: False})
            os.close(r)
            os.close(w)
            p_grep.wait()
            p_vim.wait()
            sys.exit(0)

        else:
            # (TODO: xargs --no-run-if-empty is an unsupported option - but the standard behavior! - on OS X)
            cmd += ' | xargs --no-run-if-empty -0 %s' % args.editor

    if args.show:
        print(cmd)
    else:
        sys.exit(os.system(cmd))
