# BDFR GUI

A small Tkinter window around [BDFR](https://github.com/Serene-Arc/bulk-downloader-for-reddit) (the Bulk Downloader for Reddit) for downloading the posts of a set of Reddit users. You keep a list of usernames, tick the ones you want, pick a few options, and it runs `python -m bdfr download ...` for you and shows the output.

## Features

- **User list** that persists between runs. Type a name, `u/name`, or a profile URL and press Enter or Add.
- **Multi-select**: click users in the list to choose which to download this time; All / None buttons; Remove deletes the selected names from the list.
- **Presets**: save the whole set of options (selected users included) under a name; Save, Save As, Delete; the last preset used is reloaded on start.
- **Limit / Sort / Time** fields, mapped to `--limit N`, `--sort {hot,new,top,rising,controversial,relevance}` and `--time {all,hour,day,week,month,year}`. Leave a field blank and the flag is not passed. bdfr only uses `--time` together with `--sort top` or `--sort controversial`.
- **Flags** as checkboxes, each mapped to one bdfr flag:
  | Checkbox | bdfr flag |
  |---|---|
  | Submitted posts | `--submitted` |
  | Authenticate | `--authenticate` |
  | Skip duplicates | `--no-dupes` |
  | Search existing files | `--search-existing` |
  | Verbose log | `--verbose` |
- **Extra args** passthrough: anything typed here is split shell-style and appended to the command, so any other bdfr option works (for example `--skip mp4 --max-wait-time 60`).
- **Command preview**: the exact bdfr command line is shown and updated as you change options.
- **Live log** of bdfr's output while it runs, with a Clear button.
- **Stop button** that terminates the running bdfr process.

## Requirements

- Python 3.9 or newer with Tkinter. The python.org installers for Windows and macOS include it; on Debian/Ubuntu run `sudo apt install python3-tk`.
- The `bdfr` package, installed into the same Python that will run the GUI:

  ```
  python -m pip install bdfr
  ```

  or `python -m pip install -r requirements.txt`.
- Windows for the double-click experience. On any other OS run `python bdfr_gui.pyw`.

Only the standard library is used by the GUI itself.

## Running

- **Windows**: double-click `bdfr_gui.pyw`. The `.pyw` extension makes Windows run it with `pythonw.exe`, so no console window appears. bdfr itself is run hidden by the `python.exe` from the same installation and its output goes to the log pane.
- **Anywhere**: `python bdfr_gui.pyw`

If double-clicking opens the file in an editor instead, right-click it, choose *Open with*, and pick *Python* (`pythonw.exe` in your Python installation folder).

## Using it

1. **Add users.** Type a username in the box under the list and press Enter. `u/name`, `/u/name` and `https://www.reddit.com/user/name/` are all accepted and trimmed to the bare name. Names are saved immediately.
2. **Select users.** Click names in the list to toggle them. Only selected names are downloaded. The command preview updates as you click.
3. **Choose an output folder.** Defaults to `Downloads/bdfr` in your home folder. bdfr creates it if needed and, by default, puts files in one sub-folder per subreddit, named `{REDDITOR}_{TITLE}_{POSTID}`. Those schemes can be changed with `--folder-scheme` and `--file-scheme` in Extra args.
4. **Set options.** Limit is the maximum number of posts per user. Sort and Time map to bdfr's flags as above. Leave *Submitted posts* ticked to fetch what the users posted; untick it and bdfr fetches nothing for a `--user` unless you add `--upvoted` or `--saved` in Extra args, both of which need authentication.
5. **Run.** The log shows bdfr's progress. Stop ends the process early; whatever was already downloaded stays.
6. **Save a preset** (optional). Save As asks for a name; Save overwrites the preset shown in the drop-down. The preset stores every option plus which users were selected.

### Authenticating with Reddit

Downloading a user's public submissions does not need an account. Options such as `--saved`, `--upvoted` and `--subscribed` do, and bdfr handles that itself through its `--authenticate` flag, which the *Authenticate* checkbox turns on:

1. Tick *Authenticate* and click Run.
2. bdfr prints a WARNING line in the log containing `Authenticate at https://www.reddit.com/api/v1/authorize...`. It does not open a browser for you. Select that URL in the log, copy it (Ctrl+C), and open it in a browser.
3. Log in to Reddit if asked and click *Allow*. Reddit redirects to `http://localhost:7634`, where bdfr is listening; the page confirms it worked and the download continues.
4. bdfr saves the resulting token in its own config file, so this is a one-time step. On Windows that file is `%LOCALAPPDATA%\BDFR\bdfr\default_config.cfg` (on other systems, bdfr's user config directory). Delete the `user_token` line there to sign out.

The token lives in bdfr's config, not in this GUI's config file.

## Where the config lives

`bdfr_gui_config.json`, next to `bdfr_gui.pyw`. It is created on first use and contains:

- `users`: the list of usernames, sorted case-insensitively.
- `presets`: a name-to-options map. Each preset holds `output`, `selected_users`, `limit`, `sort`, `time`, `extra` and one boolean per flag (`submitted`, `authenticate`, `no_dupes`, `search_existing`, `verbose`).
- `last_preset`: the preset to load on start.

Delete the file to start over. If it is ever unreadable the GUI moves it to `bdfr_gui_config.json.bak` and starts with defaults rather than overwriting it. Set the `BDFR_GUI_CONFIG` environment variable to a path to store the file somewhere else, for example when the script folder is read-only.

The file is listed in `.gitignore`; it is yours and should not be committed.

## Troubleshooting

- **"bdfr is not installed for this Python interpreter."** The GUI only looks in the Python it was started with. Install bdfr there: the log line shows the exact `... -m pip install bdfr` command to run. If you have several Pythons, make sure the one associated with `.pyw` files is the one you installed bdfr into (`py -0p` on Windows lists them).
- **Nothing happens when double-clicking, or a window flashes and vanishes.** The `.pyw` file is being opened with the wrong program, or Python is missing Tkinter. Run `python bdfr_gui.pyw` from a terminal to see the error.
- **pythonw vs python.** `pythonw.exe` has no console, which is what you want for the GUI. The GUI deliberately runs bdfr with `python.exe` from the same folder and captures its output, so you still see everything in the log. Running the GUI itself with `python.exe` also works; you just get a console window alongside it.
- **Nothing downloads / "0 submissions".** Check the preview: at least one `--user` and `--submitted` should be there. A private, suspended or banned account returns nothing. `--no-dupes` and `--search-existing` skip files that are already present, so a second run into the same folder legitimately downloads little. Try `--verbose` for detail, or run the previewed command in a terminal.
- **Errors mentioning rate limits or 429.** Reddit is throttling; wait and retry, or authenticate, which raises the limit.
- **Stop seems slow.** Stop asks bdfr to terminate and force-kills it after five seconds if it has not exited. Closing the window while a download runs does the same after a confirmation.

## Note

This is a personal tool written for one person's workflow and shared as-is. It wraps bdfr's command line rather than reimplementing anything, so bdfr's own documentation is the reference for what the flags do.

## Licence

Copyright (c) 2026 hunnaG. Released under the GNU General Public License v3.0; see [LICENSE](LICENSE).
