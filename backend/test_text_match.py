"""The screen says it; the check has to agree.

Every case here came off the real product or the run history, not from
imagination. A scenario that writes "Gidiş" against a heading rendered "GİDİŞ"
is the ordinary case on a Turkish site, and Python's own lower() says those are
different words — which is how a red step got filed against a screen the tester
was looking straight at.
"""

import text_match as t


class TestTurkishCase:
    """Python lowercases İ to i plus a combining dot, and leaves ı alone."""

    def test_the_dotted_capital_matches_its_lowercase(self):
        assert t.contains("GİDİŞ", "gidiş")
        assert t.contains("İstanbul", "istanbul")
        assert t.contains("SEYAHAT DENEYİMİ", "Seyahat Deneyimi")

    def test_the_dotless_i_is_the_same_letter_as_the_dotted_one(self):
        # An airport code is written IST on the page and ıst by a keyboard set
        # to Turkish; both are the same three letters to a reader.
        assert t.contains("IST", "ıst")
        assert t.contains("Işık", "isik")

    def test_a_scenario_typed_without_accents_matches_the_screen(self):
        # Deliberate: the screen says "Uçuş ara" and a tester writes "Ucus
        # ara". That is not a defect and no report should call it one.
        assert t.contains("Uçuş ara", "Ucus ara")
        assert t.contains("Havalimanı seçimi", "Havalimani secimi")
        assert t.contains("Yolcu Sayısı", "yolcu sayisi")

    def test_plain_ascii_still_works(self):
        assert t.contains("ECONOMY", "economy")
        assert not t.contains("ECONOMY", "business")


class TestTypography:
    def test_a_typographic_apostrophe_is_an_apostrophe(self):
        assert t.contains("İstanbul’da Stopover", "İstanbul'da")

    def test_dashes_and_ellipses_fold(self):
        assert t.contains("1.234 – TL", "1.234 - TL")
        assert t.contains("Devam ediliyor…", "Devam ediliyor...")

    def test_invisible_characters_do_not_split_a_phrase(self):
        assert t.contains("Ana​sayfa", "Anasayfa")
        assert t.contains("Uçak­bileti", "Uçakbileti")


class TestItStillSaysNo:
    """The point of the whole module is undone if it matches anything."""

    def test_different_words_do_not_match(self):
        assert not t.contains("İstanbul", "Ankara")
        assert not t.contains("Gidiş", "Dönüş")
        assert not t.contains("28 Eylül", "29 Eylül")

    def test_a_missing_word_is_missing(self):
        assert not t.contains("Nereden Nereye", "Londra")

    def test_whitespace_inside_the_phrase_is_still_required(self):
        # Forgives how the markup split the line, not what it says.
        assert not t.contains("İstanbulHavalimanı", "İstanbul Havalimanı")

    def test_nothing_matches_nothing(self):
        assert not t.contains("İstanbul", "")
        assert not t.contains("", "İstanbul")
        assert not t.contains(None, "İstanbul")


class TestReadingSeveralElementsAsOneLine:
    def test_a_phrase_split_across_siblings_matches(self):
        # The case this exists for: the site renders an airport as two nodes.
        line = t.joined(["İstanbul Havalimanı", "(IST)"])
        assert t.contains(line, "İstanbul Havalimanı (IST)")

    def test_a_label_repeated_back_to_back_is_written_once(self):
        # A node's text and its accessible name usually agree, and appending
        # both puts a phrase in the running text in an order the screen never
        # shows.
        line = t.joined(["İstanbul Havalimanı", "İstanbul Havalimanı", "(IST)"])
        assert not t.contains(line, "Havalimanı İstanbul")

    def test_it_reads_in_order(self):
        line = t.joined(["Gidiş", "5 Ekim", "Dönüş", "9 Ekim"])
        assert t.contains(line, "Gidiş 5 Ekim")
        assert not t.contains(line, "Gidiş 9 Ekim")


class TestSameThing:
    def test_equal_after_normalising(self):
        assert t.looks_like("GİDİŞ", "gidiş")
        assert not t.looks_like("Gidiş", "Gidiş Dönüş")
        assert not t.looks_like("", "")
