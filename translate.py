#!/usr/bin/env python
# This script updates strings in tintin_fw.bin file

import sys
from struct import pack, unpack

# data is a loaded tintin_fw file contents
data = ""
# datar is data to return
datar = ""

# where to write logs
log = sys.stdout

def is_valid_pointer(n):
    """ Checks if a number looks like a valid pointer """
    return n >= 0x08010000 and n < (0x08010000+len(data))

def is_string_pointer(ptr):
    """
    Checks if a number points to somthing similar to string;
    returns string (maybe empty) if it is a valid string or False otherwise
    """
    def is_string_char(c):
        return c in "\t\r\n" or (c >= ' ' and c <= '~') # tab, endline or printable latin

    if not is_valid_pointer(ptr):
        return False

    for i in range(ptr-0x08010000, len(data)):
        if data[i] == '\0':
            #return i - (ptr-0x08010000) # line ended without non-string chars, return strlen
            return data[ptr-0x08010000:i] # line ended without non-string chars, return it
        if not is_string_char(data[i]):
            return False # encountered non-string char, return False
    return False # reched end of file, return False

def find_all_strings():
    """
    Scans input file for all referenced strings.
    Returns array of tuples: (offset, value, string)
    """
    pointers = [] # tuples: offset to pointer, offset to its string, the string itself
    for i in range(0, len(data)-3, 4): # each 4-aligned int; -3 to avoid last (partial) value
        n = unpack("I", data[i:i+4])[0]
        s = is_string_pointer(n)
        if s:
            #print >>log, i,n,s
            pointers.append((i, n, s))
    return pointers

def find_pointers_to_offset(offset):
    """
    Finds all pointers to given offset; returns offsets to them
    """
    ret = []
    ptr = offset + 0x08010000
    for i in range(0, len(data)-3, 4):
        n = unpack("I", data[i:i+4])[0]
        if n == ptr:
            ret.append(i)
    return ret

def find_string_offsets(s):
    """ Returns list of offsets to given string """
    ret = []
    s = s + '\0' # string in file must end with \0 !
    i = data.find(s)
    while i != -1:
        ret.append(i)
        i = data.find(s, i+1)
    return ret

def parse_args():
    def hexarg(x):
        try:
            return x.decode("hex")
        except:
            return int(x,0)
    import argparse
    parser = argparse.ArgumentParser(
        description="Translation helper for Pebble firmware",
        epilog="Strings format:\nOriginal String:=Translated String\n"+
        "Any newlines in strings must be replaced with '\\n', any backslashes with '\\\\'.\n"+
        "Lines starting with # are comments, so if you need # at line start replace it with \\#.\n"+
        "Lines starting with ! are those which may be translated 'in place' "+
        "(for strings which have free space after them).")
    parser.add_argument("tintin", nargs='?', default="tintin_fw.bin", type=argparse.FileType("rb"),
                        help="Input tintin_fw file, defaults to tintin_fw.bin")
    parser.add_argument("output", nargs='?', default=sys.stdout, type=argparse.FileType("wb"),
                        help="Output file, defaults to stdout")
    parser.add_argument("-s", "--strings", default=sys.stdin, type=argparse.FileType("r"),
                        help="File with strings to translate, by default will read from stdin")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-t", "--txt", dest="old_format", action="store_true",
                       help="Use old (custom, text-based) format for strings")
    group.add_argument("-g", "--gettext", "--po", dest="old_format", action="store_false",
                       help="Use gettext's PO format for strings (default)")
    parser.add_argument("-x", "--exclude", "--exclude-strings", action="append", nargs=1, metavar="REF", default=[],
                        help="Don't translate strings with given reference ID (only for PO files). "+
                        "This option may be passed several times.")
    parser.add_argument("-p", "--print-only", action="store_true",
                        help="Don't translate anything, just print out all referenced strings from input file")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Disable safety checks for inplace translations")
    parser.add_argument("-r", "--range", action="append", nargs=2, metavar=("start","end"), type=lambda x: int(x,0),
                        dest="ranges",
                        help="Offset range to use for translated messages (in addition to space at the end of file). "+
                        "Use this to specify unneeded firmware parts, e.g. debugging console or disabled watchfaces. "+
                        "Values may be either 0xHex, Decimal or 0octal. This option may be repeated.")
    parser.add_argument("-R", "--range-mask", action="append", nargs=3, metavar=("start","end","size"),
                        type=hexarg, dest="ranges",
                        help="Ranges defined by signatures: START and END are hex signatures of first and last bytes "+
                        "of range. For example, -R 48656C6C6F 3031323334 0x243 will select range of 0x243 bytes "+
                        "starting with 'Hello' and ending with '12345'. "+
                        "You must always specify range size for checking.")
    parser.add_argument("-e", "--end", action="append_const", const="append", dest="ranges",
                        help="Use space between end of firmware and 0x08080000 (which seems to be the last address "+
                        "allowed) to store strings. Note that this will change size of firmware binary "+
                        "which may possible interfere with iOS Pebble app.")
    return parser.parse_args()

def read_strings_txt(f):
    strings = {}
    keys = []
    inplace = []
    for line in f:
        line = line[:-1] # remove trailing \n
        if len(line) == 0 or line.startswith('#'): # comment or empty
            continue
        line = line.replace('\\n', '\n').replace('\\#', '#').replace('\\\\', '\\') # unescape
        if not ':=' in line:
            print >>log, "Warning: bad line in strings:", line
            continue
        left, right = line.split(':=', 1)
        if not right: # empty
            print >>log, "Warning: translation is empty; ignoring:", line
            continue
        if ':=' in right:
            print >>log, "Warning: ambigous line in strings:", line
            continue
        if left.startswith('!'): # inplace translating
            left = left[1:]
            inplace.append(left)
        if left in strings:
            print >>log, "Warning: duplicate string, ignoring:", line
            print >>log, "Original: "+strings[left]
            continue
        strings[left] = right
        keys.append(left)
    return strings, keys, inplace

def read_strings_po(f, exclude=[]):
    # TODO : multiline strings w/o \n
    def parsevalline(line, kwlen): # kwlen is keyword length
        line = line[kwlen :].strip() # remove 'msgid' and spaces
        if line[0] == '"':
            if line[-1] != '"':
                print >>log, "Warning! Expected '\"' not found in line %d" % line
            line = line[1 :-1] # remove quotes
        line = line.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\') # unescape - TODO: test
        return line

    strings = {}
    keys = []
    inplaces = []

    # our scratchpad
    left = None
    right = None
    inplace = False
    ref = None

    for line in f:
        line = line[:-1] # remove tralining \n
        if len(line) == 0 : # end of record
            if ref in exclude:
                print "Line %s has ref <%s> which is requested to be excluded; skipping" % (repr(left), ref)
            elif left: # or else, if left is empty -> ignoring
                if right: # both left and right are provided
                    if left == right:
                        print >>log, "Translation = original, ignoring line %s" % left
                    elif left in keys:
                        print >>log, "Warning: ignoring duplicate line %s" % left
                    else:
                        keys.append(left)
                        strings[left] = right
                        if inplace:
                            inplaces.append(left)
                else: # only left provided -> line untranslated, ignoring
                    print >>log, "Ignoring untranslated line %s" % left
            # now clear scratchpad
            left = None
            right = None
            inplace = False
            ref = None
        elif line.startswith("#,"): # flags
            flags = [x.strip() for x in line[2 :].split(",")] # parse flags, removing leading "#,"
            if "fuzzy" in flags:
                inplace = True
            # ignore all other flags, if any
        elif line.startswith("#:"): # reference
            ref = line[2 :].strip()
        elif line.startswith("#"): # comment, etc
            pass # ignore
        elif line.startswith("msgid"):
            left = parsevalline(line, 5)
        elif line.startswith("msgstr"):
            right = parsevalline(line, 6)
        elif line.startswith("msgctxt"):
            # context = parsevalline(line, 7)
            print >>log, "Warning: string ctxt is not supported yet; ignoring"
        elif line.startswith('"'): # continuation?
            if right is not None:
                right += parsevalline(line, 0)
            elif left is not None:
                left += parsevalline(line, 0)
            else:
                print >>log, "Warning: unexpected continuation line: %s" % line
        else:
            print >>log, "Warning: unexpected line in input: %s" % line
    return strings, keys, inplaces

def translate_fw(args):
    global data, datar, log
    if args.output == log == sys.stdout:
        log = sys.stderr # if writing new tintin to sdout, print >>log, all messages to stderr to avoid cluttering

    # load source fw:
    data = args.tintin.read()
    datar = data

    ranges = []
    def addrange(start, end):
        """ Check range for clashes and then add it to ranges list """
        for r in ranges:
            if start == r[0] and end == r[1]: # duplicate
                print >>log, "### Duplicate range %x-%x, skipping." % (start, end)
                return
            if start >= r[0] and end <= r[1]: # fully inside; ignore
                print >>log, "### Range clash!! This must be an error! Range %x-%x fits within %x-%x; ignoring" % (
                    start, end, r[0], r[1])
                return
            if start <= r[0] and end >= r[1]: # fully outside; replace. FIXME : this might introduce clashes with other ranges
                print >>log, "### Range clash!! This must be an error! Range %x-%x contained in %x-%x; replacing" % (
                    start, end, r[0], r[1])
                r[0] = start
                r[1] = end
                return
            if start <= r[0] and end >= r[0]: # clash with beginning; truncate
                print >>log, "### Range clash!! This must be an error! Range %x-%x clashes with %x-%x; truncating" % (
                    start, end, r[0], r[1])
                end = r[0]
            if start <= r[1] and end >= r[1]: # clash with end; truncate
                print >>log, "### Range clash!! This must be an error! Range %x-%x clashes with %x-%x; truncating" % (
                    start, end, r[0], r[1])
                start = r[1]
        for r in ranges: # another loop for neighbours - now when we surely have no clashes
            if r[1] == start:
                print >>log, " #  Range neighbourhood, merging %x-%x to %x-%x" % (
                    start, end, r[0], r[1])
                r[1] = end
                return
            if end == r[0]:
                print >>log, " #  Range neighbourhood, merging %x-%x to %x-%x" % (
                    start, end, r[0], r[1])
                r[0] = start
                return
        ranges.append([start, end])
    for r in args.ranges:
        if len(r) == 3: # signature-specified range - convert it to offsets
            if type(r[0]) != str or type(r[1]) != str or type(r[2]) != int:
                print >>log, "-Warning: invalid range mask specification %s; ignoring" % repr(r)
                continue
            start = data.find(r[0])
            if start < 0:
                print >>log, "-Warning: starting mask %s not found, ignoring this range" % repr(r[0])
                continue
            end = start+data[start:].find(r[1])
            if end < 0:
                print >>log, "-Warning: start at 0x%X, ending mask %s not found, ignoring this range" % (start, repr(r[1]))
                continue
            length = end + len(r[1]) - start
            if length != r[2]:
                print >>log, ("-Warning: length mismatch for range %s..%s (0x%X..0x%X), expected %d, found %d; "+
                        "ignoring this range") % (repr(r[0]), repr(r[1]), start, end, r[2], length)
                continue
            addrange(start, end)
        elif len(r) == 2:
            addrange(r[0], r[1])
        elif r == "append":
            start = len(data)
            end = 0x70000
            if start < end:
                addrange(start, end)
            else:
                print >>log, "Warning: cannot append to end of file because its size is >= 0x70000 (max fw size)"
        else:
            print >>log, "?!? confused: unexpected range", r
    if ranges:
        print >>log, "Using following ranges:"
        for r in ranges:
            print >>log, " * 0x%X..0x%X (%d bytes)" % (r[0], r[1], r[1]-r[0])
    elif len(ranges) == 0:
        print >>log, "WARNING: no usable ranges!"

    if args.print_only:
        print >>log, "Scanning tintin_fw..."
        ptrs = find_all_strings()
        print >>log, "Found %d referenced strings" % len(ptrs)
        for p in ptrs:
            args.output.write(p[2]+'\n')
        args.output.close()
        sys.exit(0)

    if args.old_format:
        strings, keys, inplace = read_strings_txt(args.strings)
    else:
        strings, keys, inplace = read_strings_po(args.strings, args.exclude)
    print >>log, "Got %d valid strings to translate" % len(strings)
    if not strings:
        print >>log, "NOTICE: No strings, nothing to do! Will just duplicate fw"

    for key in keys:
        val = strings[key]
        print >>log, "Processing", repr(key)
        os = find_string_offsets(key)
        if not os: # no such string
            print >>log, " -- not found, ignoring"
            continue
        mustrepoint=[] # list of "inplace" key occurances which cannot be replaced inplace
        if len(val) <= len(key) or key in inplace: # can just replace
            print >>log, " -- found %d occurance(s), replacing" % len(os)
            for o in os:
                doreplace = True
                print >>log, " -- 0x%X:" % o,
                if key in inplace and len(val) > len(key) and not args.force: # check that "rest" has only \0's
                    rest = datar[o+len(key):o+32]
                    for i in range(len(rest)):
                        if rest[i] != '\0':
                            print >>log, " ** SKIPPING because overwriting is unsafe here; use -f to override. Will try to rewrite pointers"
                            mustrepoint.append(o)
                            doreplace = False # don't replace this occurance
                            break # break inner loop
                if not doreplace:
                    continue # skip to next occurance, this will be handled later
                oldlen = len(datar)
                datar = datar[0:o] + val + '\0' + datar[o+len(val)+1:]
                if len(datar) != oldlen:
                    raise AssertionError("Length mismatch")
                print >>log, "OK" # this occurance replaced successfully
            if not mustrepoint:
                continue # everything replaced fine for that key
        # we are here means that new string is longer than old (and not an
        # inplace one - or at least has one non-inplace-possible occurance)
        # so will add it to end of tintin file or to ranges
        print >>log, " -- %s %d occurance(s), looking for pointers" % ("still have" if mustrepoint else "found", len(mustrepoint or os))
        ps = []
        for o in mustrepoint or os: # use mustrepoint if it is not empty
            newps = find_pointers_to_offset(o)
            ps.extend(newps)
            if not newps:
                print >>log, " !? String at 0x%X is unreferenced, will ignore!" % o
        if not ps:
            print >>log, " !! No pointers to that string, cannot translate!"
            continue
        print >>log, " == found %d ptrs; appending or inserting string and updating them" % len(ps)
        r = None # range to use
        for rx in ranges:
            if rx[1]-rx[0] >= len(val)+1: # this range have enough space
                r = rx
                break # break inner loop (on ranges)
        if not r: # suitable range not found
            print >>log, "** Notice: no (more) ranges available for this phrase. Will skip it."
            continue # main loop
        print >>log, " -- using range 0x%X-0x%X%s" % (r[0],r[1]," (end of file)" if r[1] == 0x70000 else "")
        newp = r[0]
        oldlen = len(datar)
        datar = datar[0:newp] + val + '\0' + datar[newp+len(val)+1:]
        if len(datar) != oldlen and r[1] != 0x70000: #70000 is "range" at the end of file
            raise AssertionError("Length mismatch")
        r[0] += len(val) + 1 # remove used space from that range
        newp += 0x08010000 # convert from offset to pointer
        newps = pack('I', newp)
        for p in ps: # now update pointers
            oldlen = len(datar)
            datar = datar[0:p] + newps + datar[p+4:]
            if len(datar) != oldlen:
                raise AssertionError("Length mismatch")
    print >>log, "Saving..."
    args.output.write(datar)
    args.output.close()
    print >>log, "Done."

if __name__ == "__main__":
    args = parse_args()
    translate_fw(args)
