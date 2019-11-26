import sys
import time
import argparse
try:
    from trellis import Trellis
except:
    print('WARNING: Could not import Trellis')
from keyboard import Keyboard
from osc import OscSooperLooper

BUTTON_PRESSED = 3
BUTTON_RELEASED = 2

# {button_name: button_index_to_trellis, ...}
BUTTON_NAME_INVERSE = {
     1:  12,  2:   8,  3:  4,  4:  0,
     5:  13,  6:   9,  7:  5,  8:  1,
    'A': 14, 'B': 10, 'C': 6, 'D': 2,
    'E': 15, 'F': 11, 'G': 7, 'H': 3
    }
BUTTON_NAME_MAP = dict((BUTTON_NAME_INVERSE[key],key) for key in BUTTON_NAME_INVERSE)

BUTTON_GROUPS = {
    'mode_buttons': ['A', 'B', 'C', 'D', 'F', 'G', 'H'],
    'track_buttons': range(1,8),
    'play/pause': ['E'],
    }

BUTTON_ACTION_MAP = {
    'A': 'oneshot',
    'B': 'save/recall',
    'C': 'clear',
    'D': 'settings',
    'E': 'play/pause',
    'F': 'record',
    'G': 'overdub',
    'H': 'mute',
    }

MODE_COLOR_MAP = {
    None: 'gray',
    'oneshot': 'green',
    'save': 'yellow',
    'recall': 'yellow',
    'clear': 'blue',
    'settings': 'gray',
    'play': 'green',
    'pause': 'yellow',
    'record': 'red',
    'overdub': 'orange',
    'mute': 'blue',
    'track': 'gray',
    'mute_on': 'blue',
    'mute_off': 'gray',
    'track_recorded': 'gray',
    'track_exists': 'darkgray',
    }

class Loop:
    def __init__(self, track, client):
        self.track = track
        self.client = client
        self.is_playing = False
        self.is_muted = False
        self.is_recording = False
        self.is_overdubbing = False
        self.stopped_overdub_id = None
        self.stopped_record_id = None
        self.has_had_something_recorded = False

    def remute_if_necessary(self):
        """
        need to re-mute if this track was initially muted
        """
        if self.is_muted:
            self.client.hit('mute', self.track)

    def mark_as_muted(self):
        """
        after a oneshot, we will be auto-muted by SL, so we deal with it
        """
        self.is_muted = True

    def toggle_record(self):
        self.is_recording = not self.is_recording
        self.client.hit('record', self.track)
        self.has_had_something_recorded = True
        if not self.is_recording:
            # just stopped recording; check if we were muted
            self.remute_if_necessary()

    def toggle_overdub(self):
        self.is_overdubbing = not self.is_overdubbing
        self.client.hit('overdub', self.track)
        self.has_had_something_recorded = True
        if not self.is_overdubbing:
            # just stopped overdubbing; check if we were muted
            self.remute_if_necessary()

    def toggle(self, mode, event_id=None):
        if mode == 'record':
            if self.stopped_record_id == event_id and event_id is not None:
                # already handled this event (preemptively)
                return
            self.toggle_record()
            return self.is_recording

        elif mode == 'overdub':
            if self.stopped_overdub_id == event_id and event_id is not None:
                # already handled this event (preemptively)
                return
            self.toggle_overdub()
            return self.is_overdubbing

        elif mode == 'pause':
            if self.is_playing:
                self.client.hit('pause_on', self.track)
            else:
                self.client.hit('pause_off', self.track)
            self.is_playing = not self.is_playing

        elif mode == 'mute':
            if self.is_muted:
                self.client.hit('mute_off', self.track)
            else:
                self.client.hit('mute_on', self.track)
            self.is_muted = not self.is_muted
            return self.is_muted

    def stop_record_or_overdub(self, event_id):
        """
        okay sorry, this is horrible, but every time a button
        is pressed, we will run this, which will check if the
        loop is recording (or overdubbing), and if it is,
        stop it, and mark the event_id that stopped it.

        the situation we need to handle is that the button
        that was pressed was a button explicitly trying to stop
        recording...so in toggle(), we only toggle something if
        the event_id's don't match
        """
        self.stopped_overdub_id = None
        self.stopped_record_id = None
        did_something = False
        if self.is_recording:
            self.toggle_record()
            self.stopped_record_id = event_id
            did_something = True
        elif self.is_overdubbing:
            self.toggle_overdub()
            self.was_stopped_overdubbing = True
            self.stopped_overdub_id = event_id
            did_something = True
        return did_something

class Looper:
    def __init__(self, client, interface, startup_color='blue',
        nloops=1,  verbose=False, button_action_map=BUTTON_ACTION_MAP,
        button_name_map=BUTTON_NAME_MAP, button_groups=BUTTON_GROUPS,
        mode_color_map=MODE_COLOR_MAP):

        self.verbose = verbose
        self.interface = interface
        self.interface.set_callback(self.button_handler)
        self.client = client
        self.client.verbose = self.verbose

        self.button_action_map = button_action_map
        self.action_button_map = dict((v,k) for k,v in button_action_map.items())
        self.nloops = nloops
        self.loops = [Loop(i, client) for i in range(nloops)]

        # define button groups
        self.button_name_map = button_name_map
        self.button_index_map = dict((v,k) for k,v in button_name_map.items())
        self.button_groups = button_groups
        for k,vs in self.button_groups.items():
            vs = [self.button_index_map[n] for n in vs]
            self.interface.define_color_group(k, vs)
        self.mode_color_map = mode_color_map

        # state variables:
        self.client.set('selected_loop_num', 0)
        self.is_playing = False
        self.mode = None
        self.modes = [None, 'record', 'overdub', 'mute', 'oneshot',
            'save', 'load', 'clear', 'settings']
        self.event_id = 0

    def add_loop(self):
        self.client.add_loop()
        self.loops.append(Loop(self.nloops, self.client))
        self.nloops = len(self.loops)

    def button_handler(self, event):
        self.event_id += 1
        if event.edge == BUTTON_PRESSED:
            event_type = 'pressed'
        elif event.edge == BUTTON_RELEASED:
            event_type = 'released'
        else:
            event_type = None

        button_number = event.number
        button_name = self.button_name_map[button_number]
        action = self.button_action_map.get(button_name, button_name)
        if self.verbose:
            print('Button {}: ({}, {}, {})'.format(event_type, action, button_number, button_name))
        self.process_button(button_number, action, event_type, self.event_id)

    def refresh_track_colors_in_mode(self):
        if self.mode in ['record', 'overdub', 'oneshot']:
            # color buttons if track exists but isn't currently being recorded to
            color_exists = self.mode_color_map['track_exists']
            color_recorded = self.mode_color_map['track_recorded']
            self.interface.un_color('track_buttons')
            for loop in self.loops:
                cur_color = None
                button_number = self.button_index_map[loop.track+1]
                if loop.is_recording or loop.is_overdubbing:
                    cur_color = self.mode_color_map[self.mode]
                elif loop.has_had_something_recorded:
                    cur_color = color_recorded
                else:
                    cur_color = color_exists
                self.interface.set_color(button_number, cur_color)

    def process_button(self, button_number, action, press_type, event_id):
        # updates happen at the time of button press
        if press_type == 'pressed':
            # any time a button is pressed, we will
            # stop any recording/overdubbing going on
            for loop in self.loops:
                loop.stop_record_or_overdub(event_id)

            # now handle
            if type(action) is int:
                self.process_track_change(action, button_number, event_id)
            else:
                self.process_mode_change(action, button_number, event_id)
            self.refresh_track_colors_in_mode()

        # below we just manage colors upon button release
        elif press_type == 'released':
            if type(action) is int:
                if self.mode in [None, 'oneshot', 'clear']:
                    # button press was a oneshot, so turn off light
                    self.interface.un_color(button_number)
                    self.refresh_track_colors_in_mode()
            else:
                if not self.is_playing:
                    if self.mode is not None and action == 'play/pause':
                        pass
                        # if self.verbose:
                        #     print('   Clearing color for play/pause')
                        # self.interface.un_color(button_number)
                    elif action in ['record', 'overdub', 'mute']:
                        if self.verbose:
                            print('   Clearing color for record/overdub/mute')
                        self.interface.un_color(button_number)

    def process_mode_change(self, mode, button_number, event_id):
        """
        the only mode that does something when pressed is 'play/pause'
        otherwise, we may need to handle button colors, but we otherwise
        just wait until a track button is pressed to do anything
        """
        if self.verbose:
            print('   Mode change: {} -> {}'.format(self.mode, mode))

        if mode == 'play/pause': # applies to all loops
            if self.is_playing:
                # set_sync_pos so that when we un-pause, we ensure all loops are re-synced to the same timing
                self.client.hit('set_sync_pos', -1)
                self.client.hit('pause_on', -1)
                if self.mode in ['record', 'overdub', 'mute']:
                    print('   Cannot {} when paused, so exiting {} mode'.format(self.mode, self.mode))
                    self.interface.un_color('mode_buttons')
                    self.interface.un_color('track_buttons')
                    self.mode = None
            else:
                # when unpausing, 'trigger' restarts from where we paused
                self.client.hit('trigger', -1)
                # but we must now check which tracks were muted and re-mute
                for loop in self.loops:
                    loop.remute_if_necessary()
            self.is_playing = not self.is_playing
            color = self.mode_color_map['play'] if self.is_playing else self.mode_color_map['pause']
            self.interface.set_color(button_number, color)
            return

        if mode == self.mode:
            if self.verbose:
                print('   Already in this mode, so setting mode to None.')
            self.mode = None
            self.interface.un_color('mode_buttons')
            self.interface.un_color('track_buttons')
            return

        if mode == 'save/recall': # toggles
            if previous_mode == 'save':
                mode = 'recall'
            else:
                mode = 'save'

        if mode in ['record', 'overdub', 'mute'] and not self.is_playing:
            color = self.mode_color_map[mode]
            self.interface.set_color(button_number, color)
            print('   Cannot {} when paused; otherwise loops will get out of sync!'.format(mode))
            return

        # changing to any other type of mode clears all buttons (except play/pause)
        self.interface.un_color('mode_buttons')
        self.interface.un_color('track_buttons')
        previous_mode = self.mode
        self.mode = mode
        color = self.mode_color_map[mode]
        self.interface.set_color(button_number, color)

        if mode == 'mute':
            for loop in self.loops:
                button_number = self.button_index_map[loop.track+1]
                if not loop.has_had_something_recorded:
                    color = self.mode_color_map['track_exists']
                elif loop.is_muted:
                    color = self.mode_color_map['mute_on']
                else:
                    color = self.mode_color_map['mute_off']
                self.interface.set_color(button_number, color)

        elif mode == 'clear':
            print('   Clear mode not implemented yet.')

        elif mode == 'settings':
            print('   Settings mode not implemented yet.')

        elif mode == 'save':
            print('   Save mode not implemented yet.')
        
        elif mode == 'recall':
            print('   Recall mode not implemented yet.')

    def process_track_change(self, track, button_number, event_id):
        """
        actions depend on what mode we're in
        we also set button color based on the mode
        """
        if self.verbose:
            print('   ({}) track = {}'.format(self.mode, track))
        color = self.mode_color_map['track']

        if self.mode == None:
            self.interface.set_color(button_number, color)
            if track > self.nloops:
                print('   Creating new loop: {}'.format(self.nloops+1))
                self.add_loop()

        elif self.mode == 'oneshot':
            # warning: once you hit this once, this loop will forever
            # be out of sync; this is because we cannot store the sync_pos
            # and then restore it later
            if track <= self.nloops:
                # reset_sync_pos so that it always plays from the top
                self.client.hit('reset_sync_pos', track-1)
                self.client.hit(self.mode, track-1)
                self.interface.set_color(button_number, color)
                # if will auto-mute when done, so let's just mark this
                # because we just have to deal with what SL wants
                self.loops[track-1].mark_as_muted()
            else:
                print('   Creating new loop: {}'.format(self.nloops+1))
                self.add_loop()
                # print('   Loop index does not exist for '.format(self.mode))

        elif self.mode in ['record', 'overdub']:
            if track <= self.nloops:
                self.loops[track-1].toggle(self.mode, event_id)
            else:
                print('   Loop index does not exist for '.format(self.mode))

        elif self.mode == 'mute':
            if track <= self.nloops:
                is_muted = self.loops[track-1].toggle(self.mode)
                color_mode = 'mute_on' if is_muted else 'mute_off'
                if not is_muted and not self.loops[track-1].has_had_something_recorded:
                    color_mode = 'track_exists'
                color = self.mode_color_map[color_mode]
                self.interface.set_color(button_number, color)
            else:
                print('   Loop index does not exist for '.format(self.mode))

        elif self.mode == 'save':
            print('   Save not implemented yet.')
            self.interface.set_color(button_number, color)

        elif self.mode == 'load':
            # if we press a track that isn't an option, do nothing
            print('   Load track not implemented yet.')
            self.interface.un_color(button_number)

        elif self.mode == 'clear':
            # if we press a track that isn't an option, do nothing
            print('   Clear track not implemented yet.')
            self.interface.un_color(button_number)

        elif self.mode == 'settings':
            print('   Settings track not implemented yet.')
            self.interface.un_color(button_number)

    def start(self):
        """
        if this doesn't work, is multithreading what we want?
            1. trellis: syncs, pauses every 0.02, triggers callbacks
            2. looper: processes any callbacks triggered by trellis
        """
        self.client.load_empty_session()
        
        # show paused color initially to show we're ready
        color = self.mode_color_map['pause']
        self.interface.set_color_of_group('play/pause', color)

        if self.verbose:
            print('Looper on!')
        try:
            while True:
                self.interface.sync()
                time.sleep(.02)
        except KeyboardInterrupt:
            # Properly close the system.
            self.terminate()

    def terminate(self):
        if self.verbose:
            print()
            print('Ending looper...')
        self.client.terminate()
        self.interface.terminate()
        if self.verbose:
            print('See ya!')

def main(args):
    # connect to SooperLooper via OSC
    if args.verbose:
        print('Setting up Sooper Looper OSC client...')
    client = OscSooperLooper(client_url=args.osc_url,
        empty_session=args.empty_session_file)

    # connect with either trellis PCB or keyboard
    if args.verbose:
        print('Initializing {} interface...'.format(args.interface))
    if args.interface == 'trellis':
        interface = Trellis(startup_color=args.color)
    elif args.interface == 'keyboard':
        interface = Keyboard(BUTTON_PRESSED, BUTTON_RELEASED)

    looper = Looper(client=client,
        interface=interface,
        verbose=args.verbose)
    try:
        looper.start()
    except:
        looper.terminate()
        raise

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="looper")
    parser.add_argument('-v', '--verbose',
        dest='verbose', action='store_true',
        default=True)
    parser.add_argument('-i', '--interface',
        choices=['keyboard', 'trellis'],
        default='trellis')
    parser.add_argument('-c', '--color', type=str,
        choices=['purple', 'red', 'gray', 'green',
        'blue', 'orange'], default='blue')
    parser.add_argument('-o', '--osc_url', type=str,
        default='127.0.0.1')
    parser.add_argument('--empty_session_file', type=str,
        default='static/empty_session.slsess')
    args = parser.parse_args()
    main(args)