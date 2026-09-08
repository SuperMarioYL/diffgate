import json
from diffgate.verifier import EditClaim,ClaimedAction,verify
before='def foo():\n    return 1\n'
for name,after in [('unchanged',before),('renamed','def bar():\n    return 1\n')]:
 verdict=verify(EditClaim(before_blob=before,after_blob=after,language='python',claimed_actions=[ClaimedAction(kind='rename',symbol='foo',new_symbol='bar')]))
 print(json.dumps({'case':name,'passed':verdict.passed,'mismatches':[m.reason for m in verdict.mismatches]}))
