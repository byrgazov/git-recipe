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

from subprocess import Popen
from subprocess import PIPE
from shutil import rmtree

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


def uninstaller(part, options):
    """
    @todo: fix options['__buildout_installed__']
    @todo: keep_files = [...]
    """


class GitRecipe(object):
    """Simple recipe for fetch code form remote repository, using system git"""

    def __init__(self, buildout, name, options):
        self.options, self.buildout = options, buildout

        if 'repository' not in self.options:
            raise UserError('Repository url must be provided')

        self.url = options['repository']
        self.ref = options.get('ref', 'origin/master')

        self.as_egg = options.get('as_egg', 'false').lower() == 'true'
        self.options['download-directory'] = options.get('download-directory')\
            or buildout['buildout']['parts-directory']

        # determine repository name
        self.repo_name = options.get('repo_name', get_reponame(self.url))
        self.repo_path = os.path.join(self.options['download-directory'], self.repo_name)

        self.options['location'] = self.repo_path

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
                else:
                    # if repository exists but not the same, delete all files there
                    rmtree(self.repo_path, ignore_errors=True)  ################

            # in fact, the install
            if not installed:
                olddir = os.getcwd()
                os.chdir(self.options['download-directory'])
                try:
                    self.git('clone',    (self.url, self.repo_name))
                    self.git('checkout', (self.ref,))
                finally:
                    os.chdir(olddir)

        except UserError:
            # should manually clean files because buildout thinks that no files
            # created
            if os.path.exists(self.repo_path):
                rmtree(self.repo_path)  ########################################
            raise

        if self.as_egg:
            self._install_as_egg()

        return self._list_git_files()

    def update(self):
        """Update repository rather than download it again"""

        if self.buildout['buildout'].get('offline').lower() == 'true' or \
                self.options.get('newest', 'true').lower() == 'false':
            return

        return self.install()

    def _install_as_egg(self):
        """Install clone as development egg."""

        target = self.buildout['buildout']['develop-eggs-directory']

        for path in (self.paths or '.').split():
            path = os.path.join(self.repo_path, path.strip())
            easy_install.develop(path, target)

    def _list_git_files(self):
        git_files = ['.git'] + self.git('ls-files').splitlines()
        git_files = [os.path.join(self.repo_path, path) for path in git_files]
        # @xxx: будет оставаться мусор в виде пустых директорий
        # @see: L{uninstaller}
        return git_files
