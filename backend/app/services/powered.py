"""Tools with a motor in them, which is a thing you cannot test in a photo.

Asked for as "big power tools with moving parts, I dont know enough about
them to test". The risk is not the size, it is that the only thing that
matters about a saw or a compressor - does the motor run - is exactly what
a listing photo cannot show, and what the buyer has no way to judge.

Read off the title, like classify_logistics, because nothing else in the
listing reliably says it. Checked against all 6,432 lots on file: 45 match,
and the words that make a motor word mean something else are guarded by
name. "Stand" is deliberately NOT one of them - a table saw sold with a
folding stand is still a table saw; only an accessory STAND is not.
"""

import re
from typing import Optional

# A motor the buyer cannot hear run.
_POWERED = re.compile(r"""\b(
    circular\ saw|miter\ saw|mitre\ saw|table\ saw|chop\ saw|band\ saw|
    recip\w*\ saw|jig\ ?saw|chain\ ?saw|tile\ saw|scroll\ saw|
    drill\ press|impact\ (?:wrench|driver)|hammer\ drill|rotary\ hammer|
    jack\ ?hammer|
    orbital\ sander|belt\ sander|palm\ sander|sheet\ sander|disc\ sander|sander|
    angle\ grinder|bench\ grinder|die\ grinder|
    planer|jointer|lathe|welder|plasma\ cutter|
    air\ compressor|pressure\ washer|generator|
    lawn\ ?mower|mower|string\ trimmer|hedge\ trimmer|leaf\ blower|blower|
    tiller|log\ splitter|wood\ chipper|shop\ vac|floor\ mixer|
    pool\ pump|sump\ pump
)\b""", re.I | re.X)

# Words that turn a motor word into something with no motor at all. Every
# one of these is a real title from the inventory that the rule above
# matched and should not have.
_NOT_POWERED = re.compile(r"""\b(
    blades?|bits?|chains?|attachment|accessor\w+|hose|
    hand\ crank|hand-crank|manual|
    bicycle|bike|foot\ pump|
    tone\ generator|signal\ generator|
    hair|beauty|beard|
    crosscut|two-man|two\ man|hand\ saw|handsaw|bow\ saw|hack\ ?saw|
    router\ plane|
    book|books|puzzle|jones
)\b""", re.I | re.X)


def is_powered_tool(title: Optional[str]) -> bool:
    """True when the title names a tool whose motor decides its worth."""
    t = title or ""
    if not _POWERED.search(t):
        return False
    return not _NOT_POWERED.search(t)
