# Harmless application fixture, using the system's matched Tcl/Tk runtime.
package require Tk
if {$argc != 2} { error "usage: wish live_target.tcl OUTPUT COLOR" }
set output [lindex $argv 0]
set color [lindex $argv 1]
set clicks 0
set scrolls 0
set drags 0
wm title . "Linux computer-use verification"
wm geometry . 720x460+180+180
wm attributes . -topmost 1
label .title -text "Linux computer-use verification" -font {Sans 22}
pack .title -pady 20
label .help -text "Only this test window receives clicks, typing, scrolling, and dragging."
pack .help
canvas .target -width 400 -height 100 -background $color -highlightthickness 0
pack .target -pady 20
entry .text -font {Sans 18} -width 36
pack .text -pady 10
label .status -text "Waiting for desktop sharing approval" -wraplength 680
pack .status -pady 20
proc save {} {
    global output clicks scrolls drags
    set value [.text get]
    .status configure -text "Clicks: $clicks   Scrolls: $scrolls   Drags: $drags\n$value"
    set file [open [file join $output target.tmp] w]
    fconfigure $file -encoding utf-8 -translation lf
    puts $file $clicks
    puts $file $scrolls
    puts $file $drags
    puts $file $value
    close $file
    file rename -force [file join $output target.tmp] [file join $output target.txt]
}
bind .target <Button-1> { incr clicks; focus .text; save }
bind .target <Button-4> { incr scrolls; save }
bind .target <Button-5> { incr scrolls; save }
bind .target <B1-Motion> { incr drags; save }
bind .text <KeyRelease> { save }
# Tk's Unix emacs binding otherwise interprets Ctrl+A as beginning-of-line.
bind .text <Control-Key-a> { .text selection range 0 end; .text icursor end; break }
save
