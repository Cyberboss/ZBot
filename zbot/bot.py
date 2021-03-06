import re
import functools
import requests
from twisted.words.protocols import irc
from twisted.internet import protocol
from zbot.obj_tree_searcher import TreeSearcher
from zbot.github_events import EventHandler
from zbot.github_events import EventHandlerFactory

class ZBot(irc.IRCClient):
    #Dict of command -> function to call, ugly but effective, I guess
    commands = {
        'commit'      : '_search_for_commit',
        'kek'	      : '_kek',
        'pr' 	      : '_get_pr_info',
        'sdef'	      : '_get_definition',
        'sfile'	      : '_search_for_file',
        'shatree'     : '_sha_tree',
        'shelp'	      : '_help',
        'update_tree' : '_update_sha_tree'
    }
    #Regex to search the string for #numbers or [numbers]. At least 5 digits are necessary for # and at least 4 are necessary for []
    pr_regex = re.compile('#(\d{5,})|\[(\d{4,})\]')
    #Regex to search for a file between []
    file_regex = re.compile('\[(.*\.[^#\s]*)#?(\d+)?\]')
    #Regex to search for a commit prefixed with ^
    commit_regex = re.compile('\^([0-9a-fA-F~]{5,40})')
    #Max iterations for privmsg checks
    privmsg_max_iterations = 3
    def __init__(self, config, req_api):
        self.config = config
        self.event_handler = EventHandlerFactory(config.get('webhook'))
        self.requests = req_api
        self._setup()
        self.server_name = self.config.get('server').get('name').capitalize()
        self.connected_channels = []
        super(ZBot, self).__init__()

    def _setup(self):
        info = self.config.get('info')
        self.nickname = info.get('nickname', 'ZBot')
        self.alt_nickname = info.get('alt_nickname', 'ZBot_')
        self.realname = info.get('realname', 'ZBot')
        self.username = info.get('username', 'ZBot')
        self.channels = self.config.get('channels')
        self.ignore_list = self.config.get('ignore_list')

    def signedOn(self):
        print("Sucessfully connected to", self.server_name)
        nickserv = self.config.get('nickserv')
        if nickserv.get('enabled'):
            self.msg("NickServ", "IDENTIFY {}".format(nickserv.get('password')))
        print("Attempting to join channels: ")
        for channel in self.channels:
            self.join(channel)

    def alterCollidedNick(self, nickname):
        print("{}: {} is already in use. Changing to {}".format(self.server_name, self.nickname, self.alt_nickname))
        return self.alt_nickname

    def joined(self, channel):
        print("Sucessfully joined", channel)
        self.connected_channels.append(channel)

    def privmsg(self, user, channel, message):
        print("{}: {}: {}".format(channel, user, message))
        if user.lower().split('!')[0] in self.ignore_list:
            return
        if(message.startswith("!")):
            msg_split = message[1:].split()
            try:
                if msg_split[0] in self.commands:
                    getattr(self, self.commands[msg_split[0]])(channel, user, msg_split)
            except IndexError:
                pass
        else:
            msg_split = message.split()
            current_iterations = 0
            for msg in msg_split:
                if current_iterations > self.privmsg_max_iterations:
                    break
                pr_match = re.search(self.pr_regex, msg)
                if pr_match is not None:
                    group = pr_match.group(1) or pr_match.group(2)
                    self._get_pr_info(channel, user, group, regex_used=True)
                    current_iterations += 1
                else:
                    file_match = re.search(self.file_regex, msg)
                    if file_match is not None:
                        self._search_for_file(channel, user, file_match, regex_used=True)
                        current_iterations += 1
                    else:
                        commit_match = re.search(self.commit_regex, msg)
                        if commit_match is not None:
                            self._search_for_commit(channel, user, commit_match.group(1), regex_used=True)
                            current_iterations += 1
                
    def ctcpQuery(self, user, channel, messages):
        super(ZBot, self).ctcpQuery(user, channel, messages)
        print("CTCP: {}: {}: {}".format(channel, user, messages))

    def receive_event(self, event_type, json_payload):
        event_dict = self.event_handler.new_event(event_type, json_payload)
        msg = event_dict.get('message')
        if msg is not None:
            self.send_to_channels(event_dict.get('channels'), msg)

    def send_to_channel(self, channel, message):
        """Send to a single channel"""
        print("{s} - {c}: {m}".format(s = self.server_name, c = channel, m = message))
        self.msg(channel, message)

    def send_to_channels(self, channels, message):
        """Sends to a list of channels"""
        for channel in channels:
            self.send_to_channel(channel, message)

    def send_to_all_channels(self, message):
        """Sends to all connected channels."""
        for channel in self.channels:
            self.send_to_channel(channel, message)

    ## Bot commands
    def require_arg(func):
        """The decorator allows commands that require arguments to display the help if no arg is passed"""
        @functools.wraps(func)
        def check_arg(self, *args, **kwargs):
            if kwargs.get('regex_used') is None and len(args[2]) == 1:
                args[2].insert(0, "shelp")
                return self._help(*args, **kwargs)
            return func(self, *args, **kwargs)
        return check_arg

    #Searches the configured repo for a commit and sends the github link to it if it exists.
    @require_arg
    def _search_for_commit(self, channel, user, msg_split, regex_used=False):
        """Usage: <cmd> <commit hash>"""
        if regex_used:
            commit_sha = msg_split
        else:
            commit_sha = msg_split[1]
        path = self.requests.get_commit_url(commit_sha)
        if path:
            self.send_to_channel(channel, path)

    #Searches the configured repo's tree for a file match, and sends the closest match.
    @require_arg
    def _search_for_file(self, channel, user, msg_split, regex_used=False):
        """Usage: <cmd> <file name> <#L + line number(if any)>"""
        line = None
        if regex_used:
            file_string = msg_split.group(1)
            if msg_split.group(2):
                line = "#L" + msg_split.group(2)
        elif len(msg_split) >= 3 and msg_split[2].startswith('#L'):
            file_string = msg_split[1]
            line = msg_split[2]
        path = self.requests.get_file_url(file_string, line)
        if path:
            self.send_to_channel(channel, path)

    def _sha_tree(self, channel, user, msg_split):
        """Returns the current tree's SHA."""
        self.send_to_channel(channel, "SHA: {}".format(self.requests.get_tree_sha()))

    def _update_sha_tree(self, channel, user, msg_split):
        """Updates the current tree with configured repo's latest."""
        force = False
        if len(msg_split) >= 2 and msg_split[1] == 'force': #Forces the tree to reload regardless if it's the same sha
            force = True
        old = self.requests.get_tree_sha()
        self.requests.update_tree(force)
        self.send_to_channel(channel, "Tree updated.")
        self.send_to_channel(channel, "Old: {} New: {}".format(old, self.requests.get_tree_sha()))

    #Gets the info of a certain pull request/issue by the number from the configured repository
    @require_arg
    def _get_pr_info(self, channel, user, msg_split, regex_used=False):
        """Usage: <cmd> <number>"""
        if regex_used:
            number = msg_split
        else:
            number = msg_split[1]
        pr_info = self.requests.get_pr_info(number, channel)
        if pr_info is not None:
            msg = "\"{t}\" (#{n}) by {u} - {l}".format(t = pr_info.get('title'), n = pr_info.get('number'), u = pr_info.get('user').get('login'), l = pr_info.get('html_url'))
            self.send_to_channel(channel, msg)

    @require_arg
    def _get_definition(self, channel, user, msg_split):
        """Usage: <cmd> <proc/var> <name> <parent type(if any)>"""
        # If it is a var or proc
        search_type = msg_split[1]
        # What is the proc/var you are trying to find
        thing_to_search = msg_split[2]

        # If the proc/var has a parent type
        try:
            parent_type = msg_split[3]
        except IndexError:
            parent_type = None

        which_file = TreeSearcher.find_definition(thing_to_search, search_type, parent_type)
        if not which_file:
            return None
        which_file = which_file.replace('\\', '/').split(':')
        owner = self.requests.owner
        repo = self.requests.repo
        msg = "https://github.com/{owner}/{repo}/blob/master/{file}#L{line}".format(owner=owner, repo=repo, file=which_file[0], line=which_file[1])
        self.send_to_channel(channel, msg)

    def _kek(self, channel, user, msg_split):
        """kek"""
        self.send_to_channel(channel, "kek")

    def _help(self, channel, user, msg_split):
        """Usage: <cmd> <command(or none to display all available commands)>"""
        if len(msg_split) == 1:
            final_msg = "Available commands: "
            len_c = len(self.commands)
            count = 0
            for command in self.commands:
                if count == len_c - 1:
                    final_msg += command
                else:
                    final_msg += command + ", "
                count += 1
        else:
            command = msg_split[1]
            final_msg = getattr(self, self.commands[command]).__doc__.replace("<cmd>", "!{}".format(command))
        self.send_to_channel(channel, final_msg)

class ZBotFactory(protocol.ClientFactory):
    def __init__(self, config, requests):
        super(ZBotFactory, self).__init__()
        self.requests = requests
        self.config = config #Config containing this connection's info + webhook
        server_info = self.config.get('server')
        self.name = server_info.get('name').capitalize()#Server name

    def buildProtocol(self, addr):
        self.client = ZBot(self.config, self.requests)
        self.client.factory = self
        return self.client

    def startedConnecting(self, connector):
        print("Attempting to connect on", self.name)

    def clientConnectionLost(self, connector, reason):
        connector.connect()

    def clientConnectionFailed(self, connector, reason):
        print("Connection has failed:", reason)

    def receive_event(self, event_type, json_payload):
        self.client.receive_event(event_type, json_payload)

