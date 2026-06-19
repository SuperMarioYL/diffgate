# vhs tape — DiffGate happy path (~30s).
# Render in CI via .github/workflows/demo.yml, or locally:
#   vhs docs/demo.tape   (needs the `diffgate` CLI on PATH)
Output assets/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1100
Set Height 660
Set Theme "Dracula"
Set Padding 18

Type "# 0) A coding agent claims it renamed foo -> bar. Set up before/after blobs."
Enter
Sleep 600ms
Type "printf 'def foo(x):\n    return x\n' > before.py"
Enter
Sleep 400ms

Type "# 1) The HONEST edit: it actually renamed foo to bar."
Enter
Sleep 600ms
Type "printf 'def bar(x):\n    return x\n' > after_good.py"
Enter
Sleep 400ms
Type "diffgate verify --before before.py --after after_good.py --claim 'rename foo->bar'"
Enter
Sleep 2.5s

Type "# 2) The SILENT-LIE edit: agent says 'renamed', but the file is unchanged."
Enter
Sleep 600ms
Type "cp before.py after_lie.py"
Enter
Sleep 400ms
Type "diffgate verify --before before.py --after after_lie.py --claim 'rename foo->bar'"
Enter
Sleep 2.5s

Type "# 3) Exit code is 1 -> the agent loop must retry instead of reporting success."
Enter
Sleep 600ms
Type "echo \"diffgate exit code: $?\""
Enter
Sleep 2.5s
