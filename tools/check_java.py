#!/usr/bin/env python3
"""Structural check for the Java wrapper when no JDK is available.

A full javac compile is better; this catches the mistakes that actually happen
here: a native declared in Java with no matching C symbol, the wrong JNI name
for the package, and brace/paren imbalance.

usage: check_java.py jni/dev/veritpath/Veritpath.java jni/veritpath_jni.c
"""
import re
import sys


def fail(msg):
    print("FAIL:", msg)
    return 1


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    java_path, c_path = sys.argv[1], sys.argv[2]
    java = open(java_path).read()
    c = open(c_path).read()

    pkg = re.search(r"package\s+([\w.]+);", java)
    if not pkg:
        return fail("no package declaration")
    pkg = pkg.group(1)
    cls = re.search(r"(?:public\s+)?(?:final\s+)?class\s+(\w+)", java)
    if not cls:
        return fail("no class declaration")
    cls = cls.group(1)
    print("package %s, class %s" % (pkg, cls))

    # every `native` method must have a JNI symbol in the C file
    natives = re.findall(r"private\s+static\s+native\s+[\w\[\]<>\.]+\s+(\w+)\s*\(", java)
    if not natives:
        return fail("no native methods declared")
    prefix = "Java_%s_%s_" % (pkg.replace(".", "_"), cls)
    missing = [n for n in natives if prefix + n not in c]
    if missing:
        return fail("natives with no JNI implementation: %s" % missing)
    print("natives implemented: %s" % ", ".join(natives))

    # balance check (ignores string/char literals and comments)
    stripped = re.sub(r"//[^\n]*", "", java)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
    stripped = re.sub(r'"(\\.|[^"\\])*"', '""', stripped)
    stripped = re.sub(r"'(\\.|[^'\\])*'", "''", stripped)
    for open_ch, close_ch in (("{", "}"), ("(", ")"), ("[", "]")):
        if stripped.count(open_ch) != stripped.count(close_ch):
            return fail("unbalanced %s%s (%d vs %d)" % (
                open_ch, close_ch, stripped.count(open_ch), stripped.count(close_ch)))
    print("braces/parens balanced")

    # arguments are never null-checked silently: required params must be guarded
    for required in ("payloadDir", "outputPath"):
        if required in java and ("%s == null" % required) not in java:
            return fail("%s is used but never null-checked" % required)
    print("required arguments validated")

    # the image flag must come from Image factories, never a raw string
    if re.search(r'System\.arraycopy\(images, 0, full, 1', java):
        return fail("images are still copied as raw positionals")
    print("images carry their own flags")
    # the sub-command must be argv[0]: an earlier revision passed the image
    # flags first and every call failed with "unknown command: --init-boot"
    # (?<!String\[\] ) skips the declaration itself
    for m in re.finditer(r'(?<!String\[\] )concat\(\s*([A-Za-z_][A-Za-z0-9_]*)', java):
        first = m.group(1)
        if first not in ("head",):
            return fail("concat() must take the command head first, got '%s' - "
                        "argv[0] has to be the sub-command" % first)
    print("concat() puts the sub-command first")

    if 'analyze' in java and not re.search(r'"analyze"\s*,\s*"--brief"', java):
        return fail('analyze() should build its head as {"analyze", "--brief"}')
    print("analyze/inject heads look right")

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
