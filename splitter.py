#!/usr/bin/env python3
"""
Compound Splitter

Usage:
    splitter.py [-v ...] [options] <file>...

Options:
    --help                      Display this help and exit.
    -L --lang=<...>             Specify the language [default: de].
    -v --verbose                Be verbose. Repeatable for more verbosity.
    --stopwords                 Use stopword list [default: True].
    --no-stopwords              Don't use stopword list.
    -f --force-split            Try always splitting [default: False].
    --no-force-split            Don't try always splitting.
    -M --min-freq=<...>         Minimum frequency to consider word [default: 2].
    -l --limit=<n>              Consider only the top n words in the lexicon [default: 125000].
    --ranking=<...>             Comma-seperated list of ranking methods to use.
    --cleaning=<...>            Comma-seperated list of cleaning methods to use.
    --evaluate                  Evaluate a given gold file and print the results.
    --inspect=<word>            Debugging method to see what happens to a specific word.
    -W --print-wrong            When evaluating, print every incorrect word.

Possible values for the --ranking switch are methods of the Splitter class that
start with "rank_":

- semantic_similarity
- avg_frequency
- most_known
- longest
- shortest
- no_suffixes

The default is: --ranking=most_known,semantic_similarity,shortest

Possible values for the --cleaning switch are methods of the Splitter class
that start with "clean_":

- general
- last_parts
- suffix
- prefix
- fragments

"""
import os.path
import docopt
import pickle
from sys import stderr, version_info, exit
from math import sqrt, log2 as lg
from operator import itemgetter, mul, add
from collections import Counter
from fileinput import input as fileinput
from functools import reduce
__loc__ = os.path.realpath(os.path.join(os.getcwd(),
    os.path.dirname(__file__)))


def log0(x):
    return 0 if x == 0 else lg(x)

def pairwise(iterable):
    """Iterate over pairs of an iterable."""
    i = iter(iterable)
    j = iter(iterable)
    next(j)
    yield from zip(i, j)

def wrap_functions(fns):
    # fns = [*reversed(fns)]
    def wrapper(arg):
        # print("fns:", fns)
        for fn in fns:
            # print("applying", fn)
            arg = fn(arg)
        return arg
    return wrapper

def docopt_switch(args, switch, default):
    """Workaround for docopt/docopt/51"""
    if args[switch]:
        return True
    elif args['--no' + switch[1:]]:
        return False
    else:
        return default

class Splitter(object):
    def log(self, level, *args, **kwargs):
        """Print to stderr if verbose mode is set"""
        if self.verbose >= level:
            print(*args, file=stderr, **kwargs)

    def __init__(self, *, language="de", verbose=False, args):
        """Initialize the Splitter."""
        self.verbose = verbose
        self.log(2, args)
        self.force_split = docopt_switch(args, '--force-split', False)
        self.use_stopwords = docopt_switch(args, '--stopwords', True)
        self.min_freq = int(args.get('--min-freq', '2'))
        self.words = Counter()
        self.beginnings = Counter()
        self.set_language(language)
        self.read_lexicon(limit=args.get('--limit', 125000))
        if '--ranking' in args and args['--ranking'] is not None:
            self.rankings = args['--ranking'].split(",")
        else:
            self.rankings = 'semantic_similarity', 'shortest'
        self.log(2, "Rankings:", self.rankings)
        if '--cleaning' in args and args['--cleaning'] is not None:
            self.cleanings = [x for x in args['--cleaning'].split(",") if x]
        else:
            self.cleanings = 'general', 'last_parts', 'prefix', 'fragments', 'suffix'
        self.log(2, "Cleanings:", self.cleanings)
        self.clean = wrap_functions([getattr(self, 'clean_' + method) for method in self.cleanings])
        if 'semantic_similarity' in self.rankings:
            self.read_vectors()
        else:
            self.vec = None
        if args['--inspect']:
            self.inspect = args['--inspect']
        else:
            self.inspect = None
        self.print_wrong = args['--print-wrong']

    def set_language(self, language):
        """Set the language and its binding morphemes."""
        self.lang = language
        self.negative_morphemes = []
        if language == "de":
            self.binding_morphemes = ["s", "e", "en", "nen", "ens", "es", "ns", "er", "n"]
        elif language == "sv":
            self.binding_morphemes = ["s"]
        elif language == "hu":
            self.binding_morphemes = ["ó", "ő", "ba", "ítő", "es", "s", "i", "a"]
        else:
            raise NotImplementedError()

    def read_lexicon(self, limit=None):
        """Read the language-specific lexicon."""
        if limit is not None:
            limit = int(limit)
        self.log(
                1,
                "Loading " + os.path.join(__loc__, "lex", self.lang + ".lexicon.tsv"),
                end='',
                flush=True,
                )
        with open(os.path.join(__loc__, "lex", self.lang + ".lexicon.tsv")) as f:
            for index, line in enumerate(f):
                if limit is not None and index >= limit:
                    break
                word, count = line.split()  # fix_text() removed
                if len(word) < 4:
                    continue  # filter out noise
                # if not word.isalpha(): continue
                count = int(count)
                if count < self.min_freq:
                    continue
                word = word.lower()
                self.words[word] += count
                self.beginnings[word[:6]] += count
        if self.use_stopwords:
            with open(os.path.join(__loc__, "lex", self.lang + ".stopwords.txt")) as f:
                for line in f:
                    word = line.strip()
                    if word in self.words:
                        del self.words[word]
        with open(os.path.join(__loc__, "lex", self.lang + ".suffixes.txt")) as f:
            self.suffixes = set(filter(lambda x: len(x)>2, map(str.strip, f)))
        with open(os.path.join(__loc__, "lex", self.lang + ".prefixes.txt")) as f:
            self.prefixes = set(map(str.strip, f))
        self.log(1, "...done")

    def read_vectors(self):
        """Read the vector space into self.vec."""
        with open(os.path.join(__loc__, "lex", "{lang}.vectors.pkl".format(lang=self.lang)), "rb") as f:
            self.vec = pickle.load(f)

    def not_a_binding_morpheme(self, part):
        return part not in self.binding_morphemes

    @staticmethod
    def left_slices(word, *, minlen=1):
        """Yield every possible left slice of a word, starting with minlen."""
        for i in range(minlen, len(word)+1):
            yield word[:i], word[i:]

    def splits(self, word):
        """Split a given word in all possible ways."""
        for left, right in self.left_slices(word.lower()):
            if left in self.binding_morphemes or left in self.words:
                if right:  # not the last part
                    for right in self.splits(right):
                        yield (left, *right)
                else:
                    yield (left,)
            else:  # left part isn't a word, so continue
                for nm in self.negative_morphemes:
                    if left+nm in self.words:
                        for right in self.splits(right):
                            yield (left, *right)
                    break
                else:  # nobreak
                    if right:
                        continue
                    else:
                        # maybe unless it's the last part
                        yield (left,)

    def split(self, word, *, output="tuple"):
        """Split a given word in its parts."""
        # high-level method. This basically filters the output from splits
        word = word.lower()
        if word == self.inspect:
            print("Splitting", word)
        self.log(2, "Splitting", word)
        splits = self.splits(word)
        splits = list(splits)
        self.log(2, "Splits:", splits)
        # if not splits:  # in case we change the returning of unknown things
        #     return (word,) if output == "tuple" else word

        clean = {*self.clean(splits)}
        self.log(2, "Cleaned:", clean)
        rank = self.rank(clean)
        self.log(2, "Ranked: ", rank)

        if rank:
            best = rank[0]
        else:
            best = [(word,)]

        self.log(2, "Best:", best)

        return best[-1] if output == "tuple" else self.evalify(best[-1])

    def rank(self, clean):
        """
        Given an iterable of possible splits, return a sorted list.

        The list will contain tuples of various scores, where the last element
        of the tuple will be the corresponding split.

        >>> self.rank({('krankenhaus',), ('kranken', 'haus'), ...})
        [(1024751378, 1.0, -2, ('kranken', 'haus')), (748127142, 1.0, -3, ('krank', 'en', 'haus')), ...]

        The scoring methods are defined by self.rankings, which was initialized
        by command line or key word arguments.

        """
        ranked = []
        for split in clean:
            ranked.append((*(getattr(self, 'rank_' + method)(split) for method in self.rankings), split))
        ranked.sort(reverse=True)
        if self.force_split:
            return [split for split in ranked if len(split[-1]) > 1]
        else:
            return ranked

    def clean_general(self, splits):
        self.log(2, "Cleaning (general)")
        for split in splits:
            cleaned = []
            i = 0
            last = len(split)-1
            while i <= last:
                if split[i] in self.words:
                    cleaned.append(split[i])
                elif i < len(split)-1 and split[i]+split[i+1] in self.words:
                    cleaned.append(split[i] + split[i+1])
                    i += 1
                elif i == 0 and len(split)>1 and split[i] in self.binding_morphemes:
                    cleaned.append(split[i] + split[i+1])
                    i += 1
                else:
                    cleaned.append(split[i])
                i += 1
            self.log(3, "Made it through general:", cleaned)
            yield tuple(cleaned)

    def clean_last_parts(self, splits):
        self.log(2, "Cleaning (last parts)")
        for split in splits:
            split = list(split)
            while len(split[-1]) < 4 and len(split) >= 2:
                split[-2] += split[-1]
                del split[-1]
            self.log(3, "Made it through last_parts:", split)
            yield tuple(split)

    def clean_suffix(self, splits):
        self.log(2, "Cleaning (suffix)")
        for split in splits:
            if self.inspect == ''.join(split):
                print("cleaning suffix of", split)
            split = list(split)
            while any(split[-1].startswith(suf) and len(split[-1])-2 <= len(suf) for suf in self.suffixes) and len(split) >= 2:
                split[-2] += split[-1]
                del split[-1]
                if self.inspect == ''.join(split):
                    print(split)
            self.log(3, "Made it through suffix:", split)
            yield tuple(split)

    def clean_prefix(self, splits):
        self.log(2, "Cleaning (prefix)")
        # self.log(3, "prefix-splitting", splits)
        for split in splits:
            # self.log(4, "> let's try", split)
            if any(part in self.prefixes for part in split):
                pass
            else:
                self.log(3, "Made it through prefix:", split)
                yield split

    def clean_fragments(self, splits):
        self.log(2, "Cleaning (fragments)")
        for split in splits:
            # self.log(4, "> let's try", split)
            if any(len(part)<3 and part not in self.binding_morphemes for part in split):
                pass
            else:
                self.log(3, "Made it through fragments:", split)
                yield split

    @staticmethod
    def nth_root(x, n):
        if n==2:
            return sqrt(n)
        else:
            return x**(1/n)

    def vecsim(self, left, right):
        if self.vec is not None:
            return self.vec.similarity(left[:6], right[:6])
        else:
            return 0

    def rank_avg_frequency(self, split):
        return reduce(add,
                map(lambda x: self.words[x],
                    filter(self.not_a_binding_morpheme, split)
                    )
                ) / len([s for s in split if self.not_a_binding_morpheme(s)])

    def rank_beginning_frequency(self, split):
        return reduce(add,
                      map(lambda x: self.beginnings[x[:6]],
                    filter(self.not_a_binding_morpheme, split)
                    )) / len([s for s in split if self.not_a_binding_morpheme(s)])

    def rank_longest(self, split):
        return len(split)

    def rank_no_suffixes(self, split):
       return 0 if any(part.startswith(suf) for part in split for suf in self.suffixes) else 1

    def rank_shortest(self, split):
        return -len(split)

    def rank_semantic_similarity(self, split):
        """
        Return the average semantic similarity between adjacent parts of a split.
        """
        sims = [0]  # initialize with 0
        for left, right in pairwise([x for x in split if x not in self.binding_morphemes]):
            try:
                sims.append(self.vecsim(left, right))
            except KeyError:
                sims.append(0)
        return sum(sims)/len(sims)

    def rank_most_known(self, split):
        parts = [*filter(self.not_a_binding_morpheme, split)]

        if len(parts) == 0:
            return 0

        return sum(
                1 for p in parts
                if p in self.words
                or any(
                    p+nm in self.words for nm in self.negative_morphemes
                    )
                )/len(parts)

    def evalify(self, split):
        """
        From a tuple of parts, return a string.

        Seperate compounds with plus symbols and linking morphemes with pipe
        symbols.
        """
        result = ""
        for part in split:
            result += ("|" if part in self.binding_morphemes else "+") + part
        return result[1:]

    def evaluate(self, gold_file):
        """
        Given an annotated compound list, return performance statistics.
        """
        judgements = Counter()
        error_analysis = Counter()
        with open(gold_file) as f:
            for line in f:
                for line in f:
                    try:
                        original, gold = line.lower().strip().split()
                    except Exception as e:
                        print(line)
                        raise e
                    result = self.split(original, output="eval")
                    # ignore endings:
                    if "+" in gold and "+" in result:
                        gold = gold.rsplit("+", 1)[0] + "+"
                        result = result.rsplit("+", 1)[0] + "+"
                    # count linking morphemes as part-suffixes
                    gold = gold.replace("|", "")
                    gold = gold.replace("(", "")
                    gold = gold.replace(")", "")
                    result = result.replace("|", "")
                    # error analysis:
                    if result.count("+") < gold.count("+"):
                        error_analysis['under'] += 1
                    elif result.count("+") > gold.count("+"):
                        error_analysis['over'] += 1
                        ... #over
                    # judgements:
                    if "+" not in gold:  # not a compound
                        if gold == result:
                            judgements['true negative'] += 1
                        elif "+" in result:  # we think it's a compound
                            judgements['false positive'] += 1
                            if self.print_wrong:
                                print(original, gold, result)
                        else:
                            print(original, gold, result)
                            raise RuntimeError("true negative, but still different?")
                    else:  # compound
                        if gold == result:
                            judgements['true positive'] += 1
                        elif "+" not in result:
                            judgements['false negative'] += 1
                            if self.print_wrong:
                                print(original, gold, result)
                        else:
                            judgements['incorrectly split'] += 1
                            error_analysis['wrong'] += 1
                            if self.print_wrong:
                                print(original, gold, result)

        try:
            precision = (judgements['true positive']
                    / (judgements['true positive']
                        + judgements['false positive']
                        + judgements['incorrectly split']
                        )
                    )
        except ZeroDivisionError:  # nothing was split
            precision = 1

        recall = (judgements['true positive']
                / (judgements['true positive']
                    + judgements['false positive']
                    + judgements['false negative']
                    )
                )
        accuracy = ((judgements['true positive']
                    + judgements['true negative'])
                / sum(judgements.values())
                )
        quasi_f = 2*((precision*recall)/(precision+recall))
        coverage = ((judgements['true positive'] + judgements['incorrectly split'])
                   /(judgements['true positive'] + judgements['incorrectly split'] + judgements['false negative']))
        return precision, recall, accuracy, quasi_f, coverage, error_analysis

if __name__ == '__main__':
    if version_info < (3, 5):
        print("Error: Python >=3.5 required.", file=sys.stderr)
        exit(1)
    args = docopt.docopt(__doc__)
    spl = Splitter(language=args['--lang'], verbose=args['--verbose'], args=args)
    if args['--evaluate']:
        # Fix rounding to make 2.345 mean 2.35
        L = lambda x: int(round(x+0.001, 2)*100)
        p, r, a, f, c, E = spl.evaluate(args['<file>'][0])
        print(".{} .{} .{} .{} .{}".format(*map(L, (p, r, a, f, c))), E)
    else:
        for line in fileinput(args['<file>']):
            if not line.strip():
                break
            print(line.strip(), spl.split(line.strip(), output="eval"), sep="\t")
