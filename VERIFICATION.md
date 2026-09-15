---
id: computer-use-linux-verification-2026-09-15
tags: [codex, linux, verification]
type: test-report
status: passed-with-limitations
created: '2026-09-15'
---

# Verification: Linux computer use

## Live result

The live MCP round trip passed on GNOME Wayland, 2026-09-15. The user approved
one monitor and enabled **Allow Remote Interaction** in GNOME's sharing dialog.

The test app received:

| Check | Observed result |
|---|---|
| Screenshot | Returned a 1600×900 PNG; test target was visible |
| Click | Target received the click and focused its text field |
| ASCII typing and Ctrl+A | Final text exactly `Linux computer use: verified!` |
| Scroll | 3 scroll events received |
| Drag | 20 drag motion events received |
| Closure | Explicit `stop` completed; test process exited 0 |

The two recorded button presses are the initial click and the drag's initial press.
The test explicitly binds Ctrl+A to Select All because Tk's default Unix binding
moves to the start of the line.

Receipt: [live-result.json](verification/live-result.json).
Local screenshot: `/tmp/computer-use-linux-check-6e4pd88v/result.png`.
The screenshot is excluded from the plugin bundle because it contains the desktop
surrounding the test window.

## Automated checks

22 tests pass. They cover the real subprocess `initialize` / `tools/list` /
`tools/call` path, refusal of input before consent, coordinate bounds and scaling,
frame expiry and reuse, revocation, cancellation, input releases after errors, and
rejection of non-ASCII text before any input is emitted.

Ruff lint, `ty` type checking, plugin validation, and skill validation pass.

## Limits

- ASCII text only. A live trial dropped `é` through the portal's keysym path;
  unsupported characters are now rejected explicitly before typing starts.
- Foreground input can affect the entire desktop. There is no app allow list or
  background app isolation.
- The successful case used one selected monitor. Other compositors, monitor
  arrangements, and fractional scaling require additional live checks.
- These checks establish the tested control path, not the accuracy of arbitrary
  model-driven desktop tasks.
