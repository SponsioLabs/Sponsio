# Sponsio pattern vocabulary (use ONLY these)

`arg_blacklist`
: Block tool calls whose `<param>` matches any regex.
  args: `[<tool>, <param>, [<regex>, ...]]`
  Example: `[Bash, command, ["rm\\s+-rf\\s+/"]]`

`arg_value_range`
: Numeric param must stay within `[min, max]`.
  args: `[<tool>, <param>, <min>, <max>]`

`arg_length_limit`
: String param length cap.
  args: `[<tool>, <param>, <max_chars>]`

`rate_limit`
: At most `<n>` calls per session.
  args: `[<tool>, <n>]`
  Note: `rate_limit` currently only fires on second-and-later
  calls — fine for caps ≥ 1.

`loop_detection`
: At most `<n>` consecutive calls without other tool calls in between.
  args: `[<tool>, <n>]`

`irreversible_once`
: Hard-deny — never call this tool.
  args: `[<tool>]`

`must_precede`
: Tool A must always come before tool B.
  args: `[<tool_A>, <tool_B>]`
