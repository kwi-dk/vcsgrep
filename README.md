vcsgrep: a tool for quick grepping of Hg and Git repos
======================================================

Available in two flavors (`hgg` for Mercurial and `ggit` for Git),
vcsgrep unleashes the full power of `grep` when working in any
version controlled project.

vcsgrep only looks in text files tracked by version control, efficiently
skipping binaries, symlinks, as well as build output and other temporary
files that you don't care about.


Usage
-----

    (hgg|ggit) [GREP-OPTIONS] [VCSGREP-OPTIONS] GREP-PATTERN... [FILE-PATTERN]...

vcsgrep is invoked using the name `hgg` or `ggit`, depending on the type
of repository.

Basic usage examples:

* `hgg --help` shows basic usage instructions.
* `hgg PATTERN` greps the entire Mercurial repository for the regular
  expression `PATTERN`.
* `hgg PATTERN .cpp .h` only greps C++ files.
* `hgg PATTERN Runtime .h` only greps header files beneath the Runtime
  directory.
* `hgg PATTERN -X External` skips files in the External directory.

If you're in a Git repository, simply use `ggit` instead of `hgg`.


### grep options and patterns

vcsgrep accepts basically every `grep` option (other than options for
selecting which files to search, since that is specified using vcsgrep
filename patterns instead).

Unlike `grep`, the vcsgrep return code does not indicate whether a
match was found or not. This is a limitation of how vcsgrep invokes
`grep`. For the same reason, the `-q` grep option will not work.

Other grep options that do not work (hopefully for obvious reasons)
include `--label`, `-r`/`--recursive`, `-a`/`-I`/`--binary-files`,
`-D`/`--devices` and `-d`/`--directories`.

Since vcsgrep isn't bound by the compatibility requirements of grep, it
defaults to modern _extended_ regular expressions (`grep -E` mode),
which is usually desirable. You can use the standard  `-G` grep option
to switch back to _basic_ regular expressions (the `grep` default).

Consult `man grep` and your favorite search engine for more information
about grep patterns and options.


### Filename patterns

If no filename pattern is specified, vcsgrep will search every text file
under version control. If one or more patterns are specified, only file
names matching at least one such pattern will be searched.

Prefix a file pattern with `-X` to _exclude_ files matching the pattern
instead of including them. Exclude patterns are applied after all include
patterns.

vcsgrep distinguishes between two kinds of filename patterns:

* File extension globs (any pattern starting with a period and not
  containing a slash), such as:
  * `.c`: all files in the repository having a `.c` extension
  * `.{cpp,h,asm}`: all files with a `.cpp`, `.h` or `.asm` extension
    (equivalent to specifying three separate patterns: `.cpp .h .asm`)
  * `.[ch]pp`: all files with a `.cpp` or `.hpp` extension
    (equivalent to specifying two separate patterns: `.cpp .hpp`)
  * `.*.bak`: all files ending on `.X.bak` for any `X`

* Path globs (any other pattern), such as:
  * `*.c`: all files in the repository root having a `.c` extension
  * `**.c`: all files in the repository having a `.c` extension
  * `lib`: finds all files inside the `lib` directory
  * `lib/*.c`: all `.c` files directly inside the `lib` directory
  * `lib/**.c`: all `.c` files inside the `lib` directory
  * `./.c`: a file literally called `.c` in the repository root

Unlike standard Unix globs, file names starting with a period receive no
special glob treatment, and `*.foo` will also match the name `.foo`.

When specifying more than one pattern, vcsgrep will look at the union of
files matched by all file extension globs, and _intersect_ with the union
of all files matched by path globs:

* `lib kernel **.c`: consists of three path globs; vcsgrep will search
  the union of the three, i.e. everything in `lib` and `kernel`, as well
  as every file with a `.c` extension.
* `lib kernel .c`: consists of two path globs and a file extension glob;
  vcsgrep will search the union of the two first _intersected_ with the
  set of files having a `.c` extension, i.e. only `.c` files in `lib`
  and `kernel`.

Besides the filename extension patterns (such as `.c`), which are a
vcsgrep invention, `hgg` uses standard Mercurial filename patterns to
select files. See `hg help patterns` for all the gory details, and use
`hgg --explain` to see how vcsgrep converts file extension globs and
path globs to Mercurial filename patterns.

`ggit` uses a custom pattern engine supporting the subset of Mercurial
filename patterns described above (since Git's own filename pattern
support is frankly inadequate).


### Editing the found files

Passing `--vim` or `--gvim` will open files containing a match in `vim`
or `gvim`, instead of displaying matches on standard output.

Support for additional editors can easily be added by extending the
`editor_flags` list in the beginning of the script, as well as
`editors_that_need_stdin` if the editor reads input from standard input.


### hgg-specific options

Pass `-r REV` to search only files changed since the specified revision,
or `-r R1 -r R2` to search only files changed between the two specified
revisions.


Performance
-----------

By combining the efficiency of `grep` and Linux, vcsgrep allows searches
of a repository as large as 520 MB (across 50,000 files and 10 million
lines) in only 1.5 seconds, once the disk cache is primed, on a modern
desktop PC.

Using Windows Subsystem for Linux, on the same hardware, the query takes
about 12 seconds.

Once you add a quick filename pattern like `.h .cpp` to further filter
the results, the search becomes near-instantaneous.

(Another data point: as of this writing, the Linux kernel is 780 MB,
60,000 files and 22 million lines, and takes 8 seconds to grep in its
entirety, using Linux on the aforementioned hardware.)


Requirements and installation
------------------------------

vcsgrep requires Python 3, GNU grep, GNU sed and GNU xargs. `hgg` also
requires Mercurial 3.2 or later.

As a result, it works out of the box under modern Linux distributions,
as well as under Windows Subsystem for Linux. macOS ships with none
of the above requirements out of the box, but they can be installed e.g.
using Homebrew.

Install vcsgrep itself by symlinking `vcsgrep.py` as `hgg` and/or `ggit`
in your `~/bin` or similar, then run `hgg --version` / `ggit --version`
to check that it works:

    mkdir -p ~/bin && cd ~/bin
    ln -s PATH/TO/vcsgrep.py hgg
    ln -s PATH/TO/vcsgrep.py ggit
    hgg --version


Implementation details
----------------------

### Argument parsing

The logic of determining which options are passed as-is to `grep`, and
which ones are handled by vcsgrep itself, is non-trivial. Run vcsgrep
with the `--explain` option to see how it parses a specific commandline,
and run `python3 -m doctest vcsgrep.py` to run the argument parsing unit
tests.

### The vcsgrep pipeline

For performance, vcsgrep performs no actual file processing in Python
code, but instead constructs a shell pipeline (command) consisting of
highly optimized individual programs. Run vcsgrep with the `--show`
option to see the pipeline for a specific query.

The pipeline consists of these elements:

* running `hg files`/`git ls-files` to list files under version control
* running `sed` to filter out symlink files
* running `grep` (up to two times) to filter filenames according to the
  specified patterns (only needed for `ggit`, as such filtering is a
  built-in Mercurial feature)
* running `xargs grep` to actually search through the selected files
* if `--vim`/`--gvim` is specified: running `xargs vim`/`xargs gvim`
  to open the found files

### Editors needing stdin

For opening the files in `vim` (but not `gvim`), as well as any other
editor listed in `editors_that_need_stdin`, the pipeline shown for
`--show` is actually only an approximation. When running for real,
vcsgrep will do some Python-side pipe redirection acrobatics to avoid
the use of `xargs` getting in the way of the editor reading from stdin.
