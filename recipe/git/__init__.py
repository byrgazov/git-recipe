# -*- coding: utf-8 -*-
"""
git-recipe is a small recipe that allows you to use git
repositories

[buildout]
parts = data

[data]
recipe = gitrecipe
repository = git://example.com/my-git-repo.git
rev = origin/redevlop-branch
as_egg = true

"""

import os

from zc.buildout import buildout
from zc.buildout import easy_install
from zc.buildout import UserError

import contextlib
import shutil
import shlex

from subprocess import Popen
from subprocess import PIPE

from re import search
from re import findall
from re import MULTILINE


if '.git' not in buildout.ignore_directories:
    buildout.ignore_directories += ('.git',)


def get_reponame(url):
    if ":" in url:
        url = '/' + url.rsplit(":", 1)[1]

    match = search('\/(?P<repo_name>[a-zA-Z0-9-_.]*)$', url)

    if match:
        repo_name = match.groupdict()['repo_name']
        return repo_name

    raise UserError('Can not find repository name')


def unquote_path(path):
    path, = shlex.split(path)
    return path


def quote_path(path):
    return shlex.quote(path)


def uninstaller(part, options):
    """
    @todo: fix options['__buildout_installed__']
    @todo: keep_files = [...]
    """

    print('--uninstaller-1--', options['__buildout_installed__'])
    git_files  = options['__buildout_installed__'].splitlines()
    keep_files = extract_keep_files(options)
    print('--uninstaller-2--', keep_files)
    git_files  = exclude_files(git_files, keep_files)
    options['__buildout_installed__'] = '\n'.join(git_files)
    print('--uninstaller-3--', options['__buildout_installed__'])


def extract_keep_files(options):
    basepath   = options['location']
    keep_files = []

    for filepath in options.get('keep_files', '').splitlines():
        filepath = unquote_path(filepath)  # (?) unquote (see quote below)
        keep_files.append(filepath)

    if keep_files:
        keep_files = [os.path.join(basepath, path) for path in keep_files]
        assert all(map(os.path.isabs, keep_files)), keep_files

    return keep_files


def exclude_files(files, excludes=None):
    if not excludes:
        return files

    # @todo: patterns

    excludes  = excludes or []
    keep_dirs = [path for path in excludes if os.path.isdir(path)]
    files = files[:]

    print('--exclude_files--', keep_dirs)

    for filename in list(files):
        assert os.path.isabs(filename), filename

        if filename in excludes:
            files.remove(filename)
            continue

        for basedir in keep_dirs:
            relpath = os.path.relpath(filename, basedir).split(os.path.sep)
            if relpath[0] != os.path.pardir:
                files.remove(filename)

    return files


class GitRecipe:
    """Simple recipe for fetch code form remote repository, using system git"""

    def __init__(self, buildout, name, options):
        self.options  = options
        self.buildout = buildout

        if 'repository' not in options:
            raise UserError('Repository url must be provided')

        self.url = options['repository']
        self.ref = options.get('ref', 'origin/master')

        self.as_egg = options.get('as_egg', 'false').lower() == 'true'
        options['download-directory'] = options.get('download-directory')\
            or buildout['buildout']['parts-directory']

        # determine repository name
        self.repo_name = options.get('repo_name', get_reponame(self.url))
        self.repo_path = os.path.join(options['download-directory'], self.repo_name)

        options['location'] = self.repo_path

        self.keep_files = extract_keep_files(options)
        options['keep_files'] = '\n'.join(map(quote_path, self.keep_files))  # (?) quote

        self.paths = options.get('paths', None)

    def git(self, operation, args=None, quiet=True):
        command = [operation]

        if quiet and operation not in ('ls-files',):
            command += ['-q']

        if operation not in ('clone',):
            command = ['-C', self.repo_path] + command

        command = ['git'] + command + list(args or ())

        proc   = Popen(' '.join(command), shell=True, stdout=PIPE, universal_newlines=True)
        status = proc.wait()

        if status:
            raise UserError('Error while executing %s' % ' '.join(command))

        return proc.stdout.read()

    def check_same(self):
        if not os.path.exists(self.repo_path) or not os.path.exists(os.path.join(self.repo_path, '.git')):
            return False

        try:
            origin = self.git('remote', ('get-url', 'origin'), quiet=False).rstrip()
        except Exception:
            # Git before version 2.7.0
            origin = self.git('remote', ('-v',), quiet=False)
            origin = findall('^origin\s*(.*)\s*\(fetch\)$', origin, flags=MULTILINE)[0].rstrip()

        return origin == self.url

    def install(self):
        """Clone repository and checkout to version"""
        # go to parts directory
        installed = False

        try:
            if os.path.exists(self.repo_path):
                if self.check_same():
                    # If the same repository is here, just fetch new data and checkout to revision aka update ;)
                    installed = True
                    self.git('fetch',    (self.url,))
                    self.git('checkout', (self.ref,))
                #else:
                #    # if repository exists but not the same, delete all files there
                #    #rmtree(self.repo_path, ignore_errors=True)  ################
                #    self.__delete_files()

            # in fact, the install
            if not installed:
                olddir = os.getcwd()
                os.chdir(self.options['download-directory'])
                try:
                    with self.__clean_restore_repo():
                        self.git('clone',    (self.url, self.repo_name))
                        self.git('checkout', (self.ref,))
                finally:
                    os.chdir(olddir)

        except UserError:
            # should manually clean files because buildout thinks that no files
            # created
            #if os.path.exists(self.repo_path):
            #    #rmtree(self.repo_path)  ########################################
            #    self.__delete_files()
            raise

        if self.as_egg:
            self.__install_as_egg()

        return self.__list_git_files()

    def update(self):
        """Update repository rather than download it again"""

        if self.buildout['buildout'].get('offline').lower() == 'true' or \
                self.options.get('newest', 'true').lower() == 'false':
            return

        return self.install()

    def __install_as_egg(self):
        """Install clone as development egg."""

        target = self.buildout['buildout']['develop-eggs-directory']

        for path in (self.paths or '.').split():
            path = os.path.join(self.repo_path, path.strip())
            easy_install.develop(path, target)

    def __list_git_files(self):
        git_files = ['.git'] + self.git('ls-files', ('--recurse-submodules',)).splitlines()
        git_files = [os.path.join(self.repo_path, path) for path in git_files]
        # @xxx: будет оставаться мусор в виде пустых директорий
        # @see: L{uninstaller}
        return exclude_files(git_files, self.keep_files)

    @contextlib.contextmanager
    def __clean_restore_repo(self):
        self.__delete_files()

        # @xxx: не тестировалось ;-)
        # @todo: (!) больше сообщений в случае косяков

        if os.path.isdir(self.repo_path):
            for tryno in range(20):
                temp_dir = '{}~{:04}~'.format(self.repo_path, tryno)
                if not os.path.exists(temp_dir):
                    break
            else:
                raise RuntimeError('Can\'t clean repository "{}"'.format(self.repo_path))

            shutil.move(self.repo_path, temp_dir)
            try:
                yield

                if os.path.exists(self.repo_path):
                    for srcbase, dirs, files in os.walk(temp_dir):
                        if not os.path.exists(srcbase):
                            continue

                        relbase = os.path.relpath(srcbase, temp_dir)
                        tgtbase = os.path.join(self.repo_path, relbase)

                        for name in dirs + files:
                            source = os.path.join(srcbase, name)
                            target = os.path.join(tgtbase, name)
                            if os.path.isdir(source):
                                if not os.path.isdir(target):
                                    shutil.copytree(source, target, symlinks=True)
                            else:
                                shutil.copy2(source, target, follow_symlinks=False)

                    shutil.rmtree(temp_dir)
                else:
                    shutil.move(temp_dir, self.repo_path)

            except Exception:
                shutil.rmtree(self.repo_path)
                shutil.move(temp_dir, self.repo_path)
                raise

    def __delete_files(self):
        files = self.options.get('__buildout_installed__', '').splitlines()
        if files:
            files = exclude_files(files, self.keep_files)
            self.buildout._uninstall('\n'.join(files))
