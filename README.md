# 4d-plugin-get-text-input
Wait for stop character and return buffer; for USB HID devices (e.g. barcode scanner)

![version](https://img.shields.io/badge/version-18%2B-EB8E5F)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm%20|%20win-64&color=blue)
[![license](https://img.shields.io/github/license/miyako/4d-plugin-get-text-input)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/4d-plugin-get-text-input/total)

## Manual source versioning and publication

Actions exposes two independent entries: **Bump** and **Build**.
Both are `workflow_dispatch` only and use GitHub's built-in ref selector.
Bump has one `mode` choice (`patch`, `minor`, `major`); Build has zero custom
inputs. Choose a branch for Bump; Build can select any ref. Each
checkout is pinned to the dispatch's immutable `github.sha`. Bump creates exactly
one source-only direct-child commit on the selected branch, never a tag, release,
native build, or another workflow dispatch. Run Build separately on the new commit.
Minor resets patch; major resets minor and patch.

The original committed Bump header at
`6e2158455e22184d37335edf2b1ff1cc3cf2f1bb` explicitly coordinates
`MARKETING_VERSION`, `CURRENT_PROJECT_VERSION`, and source plist versions.
Its implementation updated only the selected marketing key, leaving build and
copied source localization stale. This migration repairs only the six stale
`CURRENT_PROJECT_VERSION` values and the two source English version strings
from `2.0.1` to the existing `MARKETING_VERSION` authority, `2.0.5`; it does not
bump the version. The six target/project Development, Deployment, and Default
configurations remain coordinated. `Info.plist`'s required
`CFBundleShortVersionString = $(MARKETING_VERSION)` is preserved, and its absent
optional `CFBundleVersion` stays absent. Known `$(...)` / `${...}` marketing/build
references are preserved; unknown, cyclic, invalid, duplicate, or drifted fields
fail before writes. `English.lproj/InfoPlist.strings` is actual source, copied by
the Resources phase, so its short version and product info string validate and
co-bump. Text encodings, BOMs, endian order, per-line endings, and file modes are
preserved. Prebuilt/destination strings, including their stale UTF-16 bytes,
are not source authority and are not rewritten during migration or Bump.

Build preserves the original Windows Release/x64 MSBuild v143/SDK 10.0 job and
subsequent macOS Default universal arm64/x86_64 job, deployment override 11.0,
native project configuration, Cocoa linkage, copy phases, dependencies, assets,
and secret names. Windows uses the exact vcxproj
`test/plugins/GET TEXT INPUT.bundle/contents/windows64/GET TEXT INPUT.4DX`
output, not a recursive search. Xcode's project-level `SYMROOT` points at
`test/Plugins`; the workflow's explicit `CONFIGURATION_BUILD_DIR` fixes the
canonical bundle at `test/Plugins/GET TEXT INPUT.bundle`. Only known generated
executables, built plist/localization, signature and intermediate paths are
invalidated; bundle resources, Windows directories, dependencies, and sibling
Default resources are retained. The built plist and localization must match
selected source. Required architecture names are exact tokens; extra architectures
are allowed, as in the original policy.

Build checks authenticated, fully paginated release availability (including
drafts) and exact remote tag absence before native work and again after signing,
strict verification, both successful JSON `Accepted` notarizations, and stapling.
All refs share one publication concurrency group, without cancellation or a
default-branch-only restriction. Final source/HEAD checks intentionally allow
tracked generated bundle outputs changed by native tools; they never reset those
outputs to pretend the checkout is clean. Source version metadata must still
match the selected commit, including staged and unstaged changes. Other tracked
source/configuration/resource edits also fail; only the exact generated bundle
outputs and its generated signature files are allowed to differ.

Publication is create-only: POST a new `vX.Y.Z` tag at the selected SHA, verify it
remotely, POST a public non-prerelease with an empty body (the original workflow
had no release body or generated notes), then upload exactly
`GET TEXT INPUT-X.Y.Z.zip` and `GET TEXT INPUT-X.Y.Z.dmg` to that newly returned
release ID. Authentication, missing pages, malformed responses, races, and upload
errors fail closed. There is no reuse, PATCH, DELETE, retry, clobber, or rollback.
**Recovery:** inspect the run's retained tag, release ID, and uploaded assets.
A partial tag/release is deliberately retained; rerunning the same version
fails availability. A maintainer must assess the retained state and explicitly
resolve it outside this automation, or source-bump and publish a new version.
Never move a published tag or rely on reruns to overwrite assets.

Offline checks (no native builds, signing, notarization, 4D, or Actions dispatch):
use Python 3 with PyYAML, Git, Bash, PowerShell (`pwsh`), `actionlint`, and the
standard portable shell utilities. Native commands resolve only to closed-PATH
test stubs. The original MSBuild CMD block is checked statically, never
translated or executed.

```sh
actionlint .github/workflows/*.yml
python3 -m unittest discover -s .github/tests -v
```

# 4d-plugin-get-text-input

The plugin watches keyboard input and hands you back whatever was typed once a chosen stop character arrives, so you can drive a project method from raw keystrokes without polling a field yourself. It's built for USB HID devices that type like a keyboard — barcode scanners, magstripe/RFID readers, etc. — which typically emit a batch of characters followed by a terminator (often `Carriage return`).

Under the hood it installs a **passive** keystroke observer: a local `NSEvent` key-down monitor or a Carbon event handler on macOS (depending on build configuration), and a `WH_GETMESSAGE` hook on Windows. It does not consume or block the keystroke — the character still reaches whatever field currently has keyboard focus, in parallel with being captured here. There is exactly **one capture session active at a time**, shared across the whole plugin (see [Requirements & platform notes](#requirements--platform-notes)).

The command itself takes no return value — it configures the capture session and returns immediately; your callback method is invoked later, asynchronously, in its own new process, once the stop character is seen.

## Summary

Command | Returns | Purpose
---|---|---
[GET TEXT INPUT](#get-text-input-1) | *(none)* | Start or stop watching keystrokes for a stop character, then invoke a project method with the captured text.

**Platforms:** macOS (Intel & Apple Silicon), Windows 64-bit

---

## Requirements & platform notes

- **Only one capture session exists at a time, plugin-wide.** Calling `GET TEXT INPUT` again while a session is already running reconfigures that same shared session (new stop character, method, and arguments) rather than starting a second, independent one. It isn't scoped per-process or per-window.
- **The callback method must be a project method in the current database structure** — it cannot live in a component. The plugin resolves it by name at call time; if the name doesn't match a project method, the command fails silently (no 4D error — see [Error handling](#error-handling--troubleshooting)).
- **The callback runs asynchronously, in a new process**, separate from whichever process called `GET TEXT INPUT`. Nothing blocks waiting for the stop character; your calling method continues immediately.
- **The stop character itself is never included** in the text passed to your callback.
- **Capture is scoped to keystrokes the host app receives**, not a true system-wide/OS-level capture — both the macOS local monitor and the Windows `WH_GETMESSAGE` hook only see input directed at 4D itself.
- **On macOS**, Unicode private-use-area codepoints (`0xE000`–`0xF8FF`, used internally for function/modifier keys) are filtered out and never appended to the captured text. **On Windows**, this filter does not exist — no codepoint range is excluded.
- **All four parameters are mandatory** — there is no optional/short form of the command.

---

## GET TEXT INPUT

### Syntax

```
GET TEXT INPUT ( stopcode ; method ; textArg ; intArg )
```

The plugin manifest declares the raw signature as `GET TEXT INPUT(&T;&T;&T;&L)` — three Text parameters followed by a Longint — which matches the four parameters read by the command's implementation.

Parameter | Type | Description
---|---|---
`stopcode` | Text | The character to watch for. Pass an empty string (`""`) to stop the current capture session instead of starting one; any other value is truncated to its first character.
`method` | Text | Name of the project method to invoke once `stopcode` is seen. Must exist in the current structure (not a component) — see [Requirements & platform notes](#requirements--platform-notes).
`textArg` | Text | Passed through unchanged to your callback as its second parameter (`$2`). Typically a process/worker name, but it's opaque to the plugin — put whatever your callback needs.
`intArg` | Longint | Passed through unchanged to your callback as its third parameter (`$3`). Typically a form window reference.
Result | *(none)* | The command has no return value.

### Description

Calling `GET TEXT INPUT` with a non-empty `stopcode` starts (or reconfigures) the shared capture session: from that point on, every keystroke the host app receives is appended to an internal buffer. When a keystroke matches `stopcode`, the buffer's accumulated text (not including the stop character) is delivered to `method` as a new process, along with `textArg` and `intArg` unchanged:

```4d
C_TEXT($1)      // captured text
C_TEXT($2)      // your textArg, unchanged
C_LONGINT($3)   // your intArg, unchanged
```

The buffer holds up to 4096 UTF-16 code units; if that limit is reached without a stop character having arrived, the buffer is cleared and capture continues from empty (so a session can't be permanently stuck once full).

Calling `GET TEXT INPUT` with `stopcode` set to `""` stops the session: the keystroke observer is removed and the buffer is discarded. No callback fires.

**On macOS**, the underlying mechanism is a local `NSEvent` key-down monitor (when the host is built against a CGFloat-as-double configuration) or a Carbon event handler otherwise; both are installed/removed on the main process regardless of which process called the command.

**On Windows**, the mechanism is a thread-local `WH_GETMESSAGE` hook. Dead-key/IME composition state can be affected system-wide by the underlying Win32 key-translation call the hook uses, which is a property of that Win32 API rather than something this command's parameters control.

### Example

From the plugin's own `README.md`:

```4d
GET TEXT INPUT ("\r";"get_text_and_call_form";Current process name;Current form window)
```

Starting a session that hands off to a different method name, with a plain literal instead of the current process name as the opaque `textArg`:

```4d
GET TEXT INPUT ("\t";"handle_scanner_input";"Scanner A";Current form window)
```

Stopping whatever session is currently active:

```4d
GET TEXT INPUT ("";"";"";0)
```

---

## Reference callback methods

These are project methods *you* supply as the `method` parameter — not part of the plugin's own command set — shown here exactly as shipped in the plugin's demo project, because they're the two documented ways to get the captured text back into your app: pushing it into a form (`CALL FORM`) or into another process (`SET PROCESS VARIABLE` + `POST OUTSIDE CALL`).

### Callback that updates a form

From the plugin's demo project (`get_text_and_call_form.4dm`), invisible/dispatch-style so it can also be pointed at directly as an object method:

```4d
//%attributes = {"invisible":true}
C_TEXT:C284($1; $text)
C_TEXT:C284($2; $worker)
C_LONGINT:C283($3; $window)

Case of 
	: (Count parameters:C259=3)
		
		$text:=$1
		$worker:=$2
		$window:=$3
		
		CALL FORM:C1391($window; Current method name:C684; $text)
		
	: (Count parameters:C259=1)
		
		$Variable:=OBJECT Get pointer:C1124(Object named:K67:5; "Variable")
		$Variable->:=$1
		
End case 
```

The `Count parameters:C259=3` branch is the one `GET TEXT INPUT` triggers: it forwards the captured text into the form referenced by `$window` via `CALL FORM`, tagging the call with `Current method name` so the form's method can tell which callback fired it. The `Count parameters:C259=1` branch is unrelated to `GET TEXT INPUT` — it's a second entry point for using this same method as a single-parameter object method callback, writing directly into an object named `"Variable"`.

The demo project's `using_call_form.4dm` opens the corresponding demo dialog:

```4d
//%attributes = {}
Open form window:C675("CALL FORM model")
DIALOG:C40("CALL FORM model")
```

### Callback that signals another process

From the plugin's demo project (`get_text_and_call_process.4dm`):

```4d
//%attributes = {"invisible":true}
C_TEXT:C284($1; $text)
C_TEXT:C284($2; $worker)
C_LONGINT:C283($3; $window)

$text:=$1
$worker:=$2
$window:=$3

SET PROCESS VARIABLE:C370(Process number:C372($worker); Variable; $text)
POST OUTSIDE CALL:C329(Process number:C372($worker))

//CALL PROCESS(-1) is also used On Outside Call to update the display
```

Here `$2`/`textArg` is treated as the name of the target process (resolved via `Process number`), and the captured text is written into that process's `Variable` before waking it with `POST OUTSIDE CALL`. The comment in the source notes that the woken process's own `On Outside Call` handler is expected to call `CALL PROCESS(-1)` itself to refresh its display — that call isn't made by this method.

The demo project's `using_call_process.4dm` opens the corresponding demo dialog:

```4d
//%attributes = {}
Open form window:C675("CALL PROCESS model")
DIALOG:C40("CALL PROCESS model")
```

### Worked example: form callback end to end

```4d
 // In the method that opens your input form:
GET TEXT INPUT ("\r";"get_text_and_call_form";Current process name;Current form window)

 // get_text_and_call_form is invoked automatically once Return is scanned/typed,
 // and pushes the captured text into the current form via CALL FORM.
```

### Worked example: process callback end to end

```4d
 // Kick off capture, targeting a background worker process by name:
GET TEXT INPUT ("\r";"get_text_and_call_process";"Scanner Worker";0)

 // get_text_and_call_process resolves "Scanner Worker" with Process number,
 // writes the captured text into that process's Variable, and wakes it
 // with POST OUTSIDE CALL — the worker's On Outside Call method picks it up
 // from there.
```

---

## Error handling & troubleshooting

- **Unknown method name fails silently.** If `method` doesn't resolve to a project method in the current structure, `GET TEXT INPUT` does nothing when the stop character arrives — no 4D error is raised, and no callback runs. Double-check spelling and that the method isn't only defined in a component.
- **A second `GET TEXT INPUT` call reconfigures the one active session, it doesn't add a second one.** If two different parts of your app call it concurrently, whichever call happened last wins for stop character, method, and both pass-through arguments.
- **The keystroke isn't consumed.** Whatever the user is scanning/typing still lands in the normally focused field as well as being captured here — this command observes, it doesn't intercept. Design your `stopcode` and input field expectations with that in mind (e.g. a focused text field will also receive and display the raw scan).
- **Buffer resets silently at 4096 characters.** An input stream that never sends `stopcode` within 4096 UTF-16 code units loses everything captured so far and starts over from empty, with no notification.
- **Stopping a session that isn't running, or starting one twice with the same parameters, are both safe no-ops** beyond the reconfiguration behavior above — there's no error for calling `GET TEXT INPUT ("";"";"";0)` when nothing is active.
- **Capture is host-app-scoped, not system-wide.** If the barcode scanner (or other HID device) types into a different application than 4D, or 4D isn't the active app, no capture happens — this is a passive, in-app observer, not a global OS hook.
- **On Windows**, the hook's underlying key-translation call can affect IME/dead-key composition state elsewhere in the OS while capture is active; if you notice odd text composition behavior in other apps while a session is running, this is the mechanism to check first.

---

## Quick reference

```4d
 // Start capturing until Return, hand off to a form-updating callback
GET TEXT INPUT ("\r";"get_text_and_call_form";Current process name;Current form window)

 // ...or hand off to a process-signaling callback instead
GET TEXT INPUT ("\r";"get_text_and_call_process";"Scanner Worker";0)

 // Stop capturing
GET TEXT INPUT ("";"";"";0)
```
