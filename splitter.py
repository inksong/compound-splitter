#!/usr/bin/env python3
"""
Compound Splitter

Usage:
    splitter.py [options] <file>

Options:
    --lang=<lang>      Specify the language [default: de].
    -v                 Be verbose
    --stopwords=<sw>   Use stopword list [default: yes].
    --force-split=<fs> Try always splitting [default: no].
    --use-counts=<uc>  Use frequencies to rank splitting [default: yes].
    --use-vectors=<uv> Use vectors to rank splitting [default: yes].
    --min-freq=<mf>    Minimum frequency to consider word [default: 2].
    --encoding=<enc>   Encoding the lexicon uses [default: latin-1].

"""
from __future__ import print_function, division, unicode_literals
import os.path
import docopt
import pickle
from sys import stderr
from math import sqrt, log2 as lg
from operator import itemgetter, mul
from collections import Counter
from ftfy import fix_text
from fileinput import input as fileinput
from functools import reduce
__loc__ = os.path.realpath(os.path.join(os.getcwd(),
    os.path.dirname(__file__)))

def log(*args, **kwargs):
    """Print to stderr."""
    print(*args, file=stderr, **kwargs)

def log0(x):
    return 0 if x == 0 else lg(x)

def pairwise(iterable):
    """Iterate over pairs of an iterable."""
    i = iter(iterable)
    j = iter(iterable)
    next(j)
    yield from zip(i, j)

class Splitter(object):
    def __init__(self, *, language="de", verbose=False, args):
        """Initialize the Splitter."""
        self.verbose = verbose
        self.args = args
        if '--force-split' in self.args:
            self.force_split = self.args['--force-split'] == 'yes'
        else:
            self.force_split = False
        if '--use-counts' in self.args:
            self.use_counts = self.args['--use-counts'] == 'yes'
        else:
            self.use_counts = True
        if '--stopwords' in self.args:
            self.use_stopwords = self.args['--stopwords'] == 'yes'
        else:
            self.use_stopwords = True
        if '--encoding' in self.args:
            self.encoding = self.args['--encoding']
        else:
            self.encoding = 'latin-1'
        if '--use-vectors' in self.args:
            self.use_vectors = self.args['--use-vectors'] == 'yes'
        else:
            self.use_vectors = True
        self.min_freq = int(self.args.get('--min-freq', '2'))
        self.words = Counter()
        self.set_language(language)
        self.read_lexicon()
        if self.use_vectors:
            self.read_vectors()

    def set_language(self, language):
        """Set the language and its binding morphemes."""
        self.lang = language
        if language == "de":
            self.binding_morphemes = ["s", "e", "en", "nen", "ens", "es", "ns", "er", "n"]
            self.negative_morphemes = []#,"en,  "n"]
        elif language == "sv":
            self.binding_morphemes = ["s"]
            self.negative_morphemes = []
        elif language == "hu":
            self.binding_morphemes = ["ó", "ő", "ba", "ítő", "es", "s", "i", "a"]
            self.negative_morphemes = []
        else:
            raise NotImplementedError()

    def read_lexicon(self):
        """Read the language-specific lexicon."""
        with open(os.path.join(__loc__, "lex", "words-with-counts-medical." + self.lang), encoding=self.encoding) as f:
            for line in f:
                try:
                    count, word = fix_text(line).split()
                except ValueError:
                    continue  # filter noise
                if len(word) < 4: continue  # filter out noise
                if not word.isalpha(): continue
                count = int(count)
                if count < self.min_freq: continue
                self.words[word.lower()] += count
        try:
            if self.use_stopwords:
                with open(os.path.join(__loc__, "lex", "stop." + self.lang)) as f:
                    for line in f:
                        word = fix_text(line.strip())
                        if word in self.words:
                            del self.words[word]
        except:
            pass
        if self.use_stopwords:
            with open(os.path.join(__loc__, "lex", "suffixes." + self.lang)) as f:
                self.suffixes = set(map(str.strip, f))
            with open(os.path.join(__loc__, "lex", "prefixes." + self.lang)) as f:
                self.prefixes = set(map(str.strip, f))
        else:
            self.suffixes = set()
            self.prefixes = set()

    def read_vectors(self):
        """Read the vector space into self.vec."""
        with open(os.path.join(__loc__, "vec", "emea.{lang}.pkl".format(lang=self.lang)), "rb") as f:
            self.vec = pickle.load(f)

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
        if self.verbose:
            log("Splitting", word)
        word = word.lower()
        splits = self.splits(word)
        if not splits:  # in case we change the returning of unknown things
            return (word,) if output == "tuple" else word
        clean = set()
        # Cleaning:
        for split in splits:
            if self.verbose:
                log("Possible split:", split)
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

            # endings aren't binding morphemes:
            # while cleaned[-1] in self.binding_morphemes:
            while len(cleaned[-1]) < 3:
                cleaned[-2] += cleaned[-1]
                del cleaned[-1]

            if self.use_stopwords and len(split) > 1:
                if any(split[-1].startswith(suf) and len(suf)+2 >= len(split[-1]) for suf in self.suffixes):
                    continue
            # if self.use_stopwords and len(split) > 1 and split[-1] in self.suffixes:
            #     continue
            if len(split) > 1 and any(part in self.prefixes for part in split):
                continue

            if self.verbose:
                log("Cleaned:", cleaned)
            clean.add(tuple(cleaned))

        if hasattr(self, 'custom_cleaning'):
            clean = self.custom_cleaning(clean, word)

        # Ranking:
        if hasattr(self, 'custom_ranking'):
            best = self.custom_ranking(clean, word)
        if not self.use_counts:
            best = self.most_known(clean, word)
        else:
            best = self.best_avg_frequency(clean, word)

        if self.verbose:
            log("Best:", best)

        # if self.use_stopwords and len(best) > 1 and best[-1] in self.suffixes:
        #     best = best[:-2] + (best[-2]+best[-1],)

        return best if output == "tuple" else self.evalify(best)

    @staticmethod
    def nth_root(x, n):
        if n==2:
            return sqrt(n)
        else:
            return x**(1/n)

    def vecsim(self, left, right):
        return self.vec.similarity(left[:6], right[:6])

    def best_avg_frequency(self, clean, word):
        """
        Frequency-based ranking method.

        Use the product of frequencies.
        """
        ranked = []
        for split in clean:
            frequencies = []
            for part in split:
                if part in self.binding_morphemes:
                    continue
                else:
                    frequencies.append(self.words[part])
            # rank = sqrt(sum(freq**2 for freq in frequencies))
            # rank = self.nth_root(reduce(mul, frequencies), len(frequencies)) # geometric mean
            rank = reduce(mul, frequencies)
            rank2 = sum(log0(freq) for freq in frequencies)
            if self.force_split and len([x for x in split if x not in self.binding_morphemes]) == 1:
                rank *= 0.0001
            if self.use_vectors:
                vec = self.semantic_similarity(split)
            else:
                vec = 0
            ranked.append((rank, vec, -len(split), rank2, split))
        ranked.sort(reverse=True)
        return ranked[0][-1]

    def most_known(self, clean, word):
        """
        Return the split with the highest percentage of known parts.

        When in doubt, return the split with the lower amount of parts.
        """
        ranked = []
        for split in clean:
            parts, known_parts = 0, 0
            for w in split:
                if w in self.binding_morphemes:
                    continue
                elif w in self.words or any(w+nm in self.words for nm in self.negative_morphemes):
                    known_parts += 1
                parts += 1
            if self.force_split and len([x for x in split if x not in self.binding_morphemes]) == 1:
                known_parts = 0  # enforce decompounding
            if self.use_vectors:
                vec = self.semantic_similarity(split)
            else:
                vec = 0
            ranked.append((known_parts/parts, -len(split), vec, split))

        try:
            return max(ranked)[-1]
        except ValueError:
            return word

    def semantic_similarity(self, split):
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

if __name__ == '__main__':
    args = docopt.docopt(__doc__)
    if args['-v']:
        verbose = True
    spl = Splitter(language=args['--lang'], verbose=args['-v'], args=args)
    with open(args['<file>']) as f:
        for line in f:
            line = fix_text(line)
            print(line.strip(), spl.split(line.strip(), output="eval"), sep="\t")
