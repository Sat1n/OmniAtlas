#include "util.h"

// Intentionally malformed C++ — exercises the AST_PARSE_ERROR diagnostics.
struct Broken {
  int value
};

void unfinished( {
