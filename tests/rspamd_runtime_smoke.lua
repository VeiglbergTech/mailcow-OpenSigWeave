local rspamd_util = require "rspamd_util"

local encoded = tostring(rspamd_util.encode_base64("OpenSigWeave", 0))
assert(encoded ~= "", "rspamd_util.encode_base64 returned an empty result")
assert(encoded:match("^T3BlblNpZ1dlYXZl"), "unexpected base64 output")
print("Rspamd runtime smoke passed")
