"""Tests for email_voice_check.py house HTML card gate (format_no_card).

Verifies that client emails outside the plain-letter set require a 600px card wrapper
when an HTML body is provided, fail format_unchecked when missing --html under --strict,
and leave plain-letter recipients (Greg, devops, team, william) untouched.
Uses made-up client fixtures (public repo rule).
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import email_voice_check as evc  # noqa: E402


class TestEmailVoiceCardGate(unittest.TestCase):
    """Card wrapper gate tests for client email HTML formatting."""

    CLEAN_TEXT = "I fixed the issue and verified the site."

    BARE_HTML = (
        "<div>\n"
        "  <p>I fixed the issue and verified the site.</p>\n"
        "</div>"
    )

    CARD_TABLE_HTML = (
        '<table align="center" width="600" style="margin: 0 auto; background-color: #ffffff; border-collapse: collapse;">\n'
        '  <tr>\n'
        '    <td style="padding: 24px;">\n'
        '      <p>I fixed the issue and verified the site.</p>\n'
        '    </td>\n'
        '  </tr>\n'
        '</table>'
    )

    CARD_DIV_HTML = (
        '<div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 24px;">\n'
        '  <p>I fixed the issue and verified the site.</p>\n'
        '</div>'
    )

    CARD_STYLE_WIDTH_HTML = (
        '<table style="width: 600px; margin: 0 auto; background: #ffffff;">\n'
        '  <tr><td><p>I fixed the issue and verified the site.</p></td></tr>\n'
        '</table>'
    )

    def test_bare_div_p_fails_under_strict(self):
        """A bare <div><p> body fails format_no_card under --strict for a non-plain-letter client."""
        res = evc.detect_llm_signals(
            self.CLEAN_TEXT,
            strict=True,
            recipient='acme_corp',
            html=self.BARE_HTML,
        )
        self.assertEqual(res['verdict'], 'FAIL')
        house_cats = [cat for cat, _ in res['house']]
        self.assertIn('format_no_card', house_cats)
        msg = dict(res['house'])['format_no_card']
        self.assertIn(evc.CARD_SOURCE, msg)

    def test_bare_div_p_warns_under_non_strict(self):
        """A bare <div><p> body warns only under non-strict for a client."""
        res = evc.detect_llm_signals(
            self.CLEAN_TEXT,
            strict=False,
            recipient='acme_corp',
            html=self.BARE_HTML,
        )
        self.assertEqual(res['verdict'], 'PASS')
        house_cats = [cat for cat, _ in res['house']]
        self.assertNotIn('format_no_card', house_cats)
        warning_cats = [cat for cat, _ in res['warnings']]
        self.assertIn('format_no_card', warning_cats)
        msg = dict(res['warnings'])['format_no_card']
        self.assertIn(evc.CARD_SOURCE, msg)

    def test_600px_card_table_passes(self):
        """A 600px table card body passes without format_no_card."""
        res = evc.detect_llm_signals(
            self.CLEAN_TEXT,
            strict=True,
            recipient='acme_corp',
            html=self.CARD_TABLE_HTML,
        )
        self.assertEqual(res['verdict'], 'PASS')
        house_cats = [cat for cat, _ in res['house']]
        self.assertNotIn('format_no_card', house_cats)
        warning_cats = [cat for cat, _ in res['warnings']]
        self.assertNotIn('format_no_card', warning_cats)

    def test_600px_card_container_div_passes(self):
        """A 600px container div card body passes without format_no_card."""
        res = evc.detect_llm_signals(
            self.CLEAN_TEXT,
            strict=True,
            recipient='acme_corp',
            html=self.CARD_DIV_HTML,
        )
        self.assertEqual(res['verdict'], 'PASS')
        house_cats = [cat for cat, _ in res['house']]
        self.assertNotIn('format_no_card', house_cats)

    def test_greg_plain_body_untouched(self):
        """Greg's plain body is untouched by the card check (no format_no_card)."""
        res = evc.detect_llm_signals(
            self.CLEAN_TEXT,
            strict=True,
            recipient='greg',
            html=self.BARE_HTML,
        )
        house_cats = [cat for cat, _ in res['house']]
        self.assertNotIn('format_no_card', house_cats)
        warning_cats = [cat for cat, _ in res['warnings']]
        self.assertNotIn('format_no_card', warning_cats)

    def test_plain_letter_recipients_exempt(self):
        """All plain-letter set members (greg, devops, team, william) are exempt from card gate."""
        for recipient in ('greg', 'devops', 'team', 'william'):
            with self.subTest(recipient=recipient):
                res = evc.detect_llm_signals(
                    self.CLEAN_TEXT,
                    strict=True,
                    recipient=recipient,
                    html=self.BARE_HTML,
                )
                house_cats = [cat for cat, _ in res['house']]
                self.assertNotIn(
                    'format_no_card',
                    house_cats,
                    f"format_no_card should not fire for {recipient}",
                )

    def test_has_card_wrapper_variants(self):
        """has_card_wrapper accurately identifies house cards (480-720 px)."""
        self.assertTrue(evc.has_card_wrapper(self.CARD_TABLE_HTML))
        self.assertTrue(evc.has_card_wrapper(self.CARD_DIV_HTML))
        self.assertTrue(evc.has_card_wrapper(self.CARD_STYLE_WIDTH_HTML))
        self.assertTrue(evc.has_card_wrapper('<section style="max-width: 600px; padding: 10px;"><p>x</p></section>'))
        self.assertTrue(evc.has_card_wrapper('<td width="600" style="background:#fff;"><p>x</p></td>'))
        # House cards are not always exactly 600: a 620px card (as sent on 6 Nov) is a card.
        self.assertTrue(evc.has_card_wrapper(
            '<table width="100%" style="background-color:#eef1f3"><tr><td>'
            '<table width="620" style="background-color:#ffffff;max-width:620px;width:100%"><tr><td>x</td></tr></table>'
            '</td></tr></table>'))
        # A fixed width with no inline style is not the house card.
        self.assertFalse(evc.has_card_wrapper('<table width="600"><tr><td>x</td></tr></table>'))
        self.assertFalse(evc.has_card_wrapper('<div style="max-width: 1200px;"><p>x</p></div>'))

        self.assertFalse(evc.has_card_wrapper(self.BARE_HTML))
        self.assertFalse(evc.has_card_wrapper('<p>just text</p>'))
        self.assertFalse(evc.has_card_wrapper('<table width="100%"><tr><td>x</td></tr></table>'))
        self.assertFalse(evc.has_card_wrapper('<div style="width: 300px;"><p>x</p></div>'))
        self.assertFalse(evc.has_card_wrapper(''))
        self.assertFalse(evc.has_card_wrapper(None))

    def test_card_wrapper_matches_helper(self):
        """card_wrapper_matches helper returns format_no_card for clients and empty for exempt."""
        # Client with bare body
        matches = evc.card_wrapper_matches(self.BARE_HTML, recipient='example_client')
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0], 'format_no_card')

        # Client with card body
        self.assertEqual(evc.card_wrapper_matches(self.CARD_TABLE_HTML, recipient='example_client'), [])

        # Plain-letter recipients with bare body
        for rcpt in ('greg', 'devops', 'team', 'william'):
            self.assertEqual(evc.card_wrapper_matches(self.BARE_HTML, recipient=rcpt), [])


if __name__ == '__main__':
    unittest.main()
