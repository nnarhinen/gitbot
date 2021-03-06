from commando.util import ShellCommand, getLoggerWithConsoleHandler
from contextlib import contextmanager
from datetime import datetime
from fswrap import Folder
from subprocess import CalledProcessError


logger = getLoggerWithConsoleHandler('gitbot.lib.git')

class GitException(Exception): pass


class Tree(object):

    def __init__(self, source, repo=None, remote='origin', branch='master'):
        if not source:
            raise Exception('You must provide the source')
        self.source = Folder(source)
        self.repo = repo
        self.remote = remote
        self.branch_name = branch
        self.tagger = Tagger(self)
        self.git = ShellCommand(cwd=self.source.path, cmd='git')

    def get_revision_remote(self):
        out = self.git.get('ls-remote', self.repo, self.branch_name, cwd='.')
        return out.split()[0]

    def get_branch(self):
        return self.git.get('rev-parse', '--abbrev-ref', 'HEAD')

    def get_revision(self, ref=None, short=True):
        self.ensure_source_exists()
        return self.git.get('rev-parse',
            '--short' if short else '',
            (ref or 'HEAD')).strip()

    def get_last_committed(self, ref=None):
        self.ensure_source_exists()
        modified = self.git.get('log',
                                (ref or 'HEAD'),
                                '-1', '--pretty=format:%ad', '--date=local')
        return datetime.strptime(modified, '%a %b %d %H:%M:%S %Y')

    def ensure_repo_exists(self):
        if not self.repo:
            raise GitException('This tree [%s] does not have a repo.'
                                % self.source.path)

    def ensure_source_exists(self):
        if not self.source.exists:
            raise GitException('The source directory [%s] is missing.'
                                % self.source.path)

    def ensure_source_does_not_exist(self):
        if self.source.exists:
            raise GitException('The source directory [%s] exists already.'
                                % self.source.path)

    def make(self, bare=False):
        self.ensure_source_exists()
        logger.info('Initializing git...')
        self.git.call('init', '--bare' if bare else None)

    def make_ref(self, ref, sha='HEAD'):
        self.ensure_source_exists()
        logger.info(
            'Creating a ref (%s) to commit (%s)...' % (
                    ref,
                    sha
                ))
        self.git.call('update-ref', ref, sha)

    def add_remote(self):
        self.ensure_source_exists()
        logger.info(
            'Adding remote repo (%s) with alias (%s)...' % (
                    self.repo,
                    self.remote
                ))

        self.git.call('remote', 'add', self.remote, self.repo)

    def clone(self, tip_only=False):
        self.ensure_source_does_not_exist()
        self.ensure_repo_exists()
        logger.info('Cloning tree...')
        self.source.parent.make()
        self.git.call('clone',
                        self.repo,
                        self.source.name,
                        '--depth' if tip_only else None,
                        '1' if tip_only else None,
                        '--branch' if tip_only else None,
                        self.branch_name if tip_only else None,
                        cwd=self.source.parent.path)
        self.checkout()

    def checkout(self, ref=None):
        self.ensure_source_exists()
        logger.info('Checking out %s...' % (ref or self.branch_name))
        self.git.call('checkout', (ref or self.branch_name))

    def new_branch(self, branch_name):
        logger.info('Setting up new branch %s...' % self.branch_name)
        self.git.call('branch', branch_name)

    @contextmanager
    def branch(self, branch_name):
        old_branch = self.branch_name
        self.branch_name = branch_name
        self.checkout()
        yield
        self.branch_name = old_branch
        self.checkout()

    def pull(self, tip_only=False):
        if self.source.exists:
            if self.has_changes(check_remote=False):
                raise GitException(
                    'Source [%s] has changes. Please sync it with the remote.'
                        % self.source)
            logger.info('Pulling remote changes ...')
            self.git.call('pull', self.remote, self.branch_name)
        else:
            self.clone(tip_only=tip_only)

    def fetch(self):
        self.ensure_source_exists()
        logger.info('Fetching source...')
        self.git.call('fetch', self.remote)

    def fetch_ref(self, ref, local=None, checkout=True, notags=False):
        if not self.source.exists:
            self.source.make()
            self.make()
            self.add_remote()
        logger.info('Fetching ref...')
        self.git.call('fetch',
                            '-n' if notags else '',
                            self.remote,
                            ref + (':' + local) if local else '')
        if local:
            self.checkout(local)

    def push(self, force=False, set_upstream=False, ref=None):
        self.ensure_source_exists()
        logger.info('Pushing changes...')
        push_ref = (ref or self.branch_name)
        self.git.call('push',
                '-f' if force else None,
                '-u' if set_upstream else None,
                self.remote,
                push_ref)

    def merge(self, ref, ff_only=True):
        self.ensure_source_exists()
        self.checkout()
        logger.info('Merging %s into %s...' % (ref, self.branch_name))
        try:
            self.git.get('merge',
                '--ff-only' if ff_only else None,
                ref)
        except CalledProcessError, called:
            message = None
            if ff_only:
                message = 'Cannot fast forward the merge.'
            else:
                message = 'Conflicts detected. Unable to merge.'
            logger.error(called.output)
            raise GitException(message)


    def reset(self, hard=True):
        self.ensure_source_exists()
        logger.info('Resetting ...')
        self.git.call('reset', '--hard' if hard else '--soft')

    def undo(self, commits=1, push=True):
        self.git.call('reset',
                        '--hard',
                        'HEAD~%d' % commits)
        if push:
            self.push(force=True)

    def add(self, what=None):
        self.ensure_source_exists()
        logger.info('Adding new files...')
        self.git.call('add', (what or '.'))

    def commit(self, message, add=True):
        self.ensure_source_exists()
        logger.info('Committing changes...')
        if add:
            self.add()
        try:
            self.git.get('commit', '-a', '-m', message)
        except CalledProcessError, called:
            logger.error(called.output)
            raise GitException('Nothing to commit.')


    def has_changes(self, check_remote=True):
        self.ensure_source_exists()
        logger.info('Checking for changes...')
        has_changes = False
        num_changes = int(self.git.get(
                            'git status --porcelain | grep "^??" | wc -l',
                            shell=True))
        logger.debug('Local changes=(%s)' % num_changes)
        has_changes = (num_changes > 0)

        if not has_changes and check_remote:
            self.fetch()
            diff = self.git.get('diff',
                            '%s/%s' % (self.remote, self.branch_name),
                            '--stat').strip()
            logger.debug('Remote changes=(%s)' % diff)
            has_changes = len(diff) > 0
        return has_changes


class Tagger(object):

    def __init__(self, tree):
        if not tree:
            raise GitException('You must provide a valid tree object')
        self.tree = tree
        self.git = ShellCommand(cwd=self.tree.source.path, cmd='git')

    def check(self, tag):
        self.tree.ensure_source_exists()
        logger.info('Checking for changes...')
        has_tag = False
        tagged = self.git.get('tag', '-l', tag)
        has_tag = (len(tagged.strip()) > 0)
        return has_tag

    def add(self, tag, message=None, push=False, sync=False):
        self.tree.ensure_source_exists()

        if sync:
            self.sync()

        logger.info('Adding tag...')

        if self.check(tag):
            raise GitException('The specified tag already exists')

        if message:
            self.git.call('tag', tag, '-a', '-m', message)
        else:
            self.git.call('tag', tag)

        if push:
            self.push()

    def remove(self, tag, push=False, sync=False):
        self.tree.ensure_source_exists()
        if sync:
            self.sync()
        logger.info('Removing tag...')

        if self.check(tag):
            self.git.call('tag', '-d', tag)

        if push:
            self.git.call('push', self.tree.remote, ':' + tag)

    def push(self):
        self.tree.ensure_source_exists()
        logger.info('Pushing all new tags to remote...')
        self.git.call('push', '--tags')

    def sync(self):
        self.tree.ensure_source_exists()
        logger.info('Syncing tags from remote...')
        logger.info('Deleting all local tags')
        self.git.call('git tag -l | xargs git tag -d', shell=True)
        self.tree.fetch()
