#!/usr/bin/env python3
"""Tests for the sender-"we" check. Run: python3 test_email_voice_check.py"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import email_voice_check as v

CLIENT = "We recommend leaving them off. What we'll do, free: a daily check that tells us when it breaks.\nJon"


class SenderWe(unittest.TestCase):
    def test_client_sentence_fails_strict(self):
        for r in ('gail', 'marcie', 'schlajo', 'someone-new'):
            res = v.detect_llm_signals(CLIENT, strict=True, recipient=r)
            self.assertEqual(res['verdict'], 'FAIL', r)
            self.assertTrue(any(c == 'sender_we' for c, _ in res['house']), r)

    def test_unnamed_recipient_fails(self):
        res = v.detect_llm_signals(CLIENT, strict=True, recipient='greg', recipient_explicit=False)
        self.assertEqual(res['verdict'], 'FAIL')

    def test_coop_is_warning_only(self):
        for r in ('greg', 'william', 'devops'):
            res = v.detect_llm_signals(CLIENT, strict=True, recipient=r)
            self.assertFalse(any(c == 'sender_we' for c, _ in res['house']), r)
            self.assertTrue(res['warnings'], r)

    def test_quoted_ignored(self):
        t = '> We need our site back, tell us when.\nOn it, I\'ll look today.\nJon'
        self.assertEqual(v.sender_we(t), [])
        self.assertEqual(v.detect_llm_signals(t, recipient='gail')['house'], [])

    def test_clients_own_words_in_quotes_ignored(self):
        t = 'You said "we want it fast" and I agree.\nJon'
        self.assertEqual(v.sender_we(t), [])

    def test_i_form_passes(self):
        t = "I recommend leaving them off. I'll set up a daily check that tells me when it breaks.\nJon"
        self.assertEqual(v.sender_we(t), [])

    def test_let_us_and_suggestion(self):
        hits = v.sender_we("Let us start Monday.\nJon")
        self.assertEqual([h[1] for h in hits], ['let me'])


GREG_REPLY = """> 1. Is the staging site back up?
Yes, it came back at 9am and I checked the login.
> 2. Did the backup run?
Yes. The last copy is from last night, 2.1 GB.
No answer needed: the cert renewed on its own.
Jon"""


class PromisesFutureWork(unittest.TestCase):
    def phrases(self, text):
        return [h[0].lower() for h in v.promises_future_work(text)]

    def test_each_phrase_hits(self):
        for s in ("I'll send it.", "I will send it.", "I can set that up.", "I could set that up.",
                  "I'd be happy to look.", "Happy to look.", "A fuller comparison, if you want it.",
                  "If you'd like, a demo.", "We plan to move it.", "Planning to move it.",
                  "It lands soon.", "Done next week.", "It is going to change.", "One option I offer."):
            self.assertTrue(v.promises_future_work(s + "\nJon"), s)

    def test_strict_fails_and_explains(self):
        res = v.detect_llm_signals("A fuller comparison if you want it.\nJon", strict=True, recipient='gail')
        self.assertEqual(res['verdict'], 'FAIL')
        msgs = [m for c, m in res['matches'] if c == 'promises_future_work']
        self.assertTrue(msgs and 'do the work first, or cut the line' in msgs[0])

    def test_not_checked_without_strict(self):
        res = v.detect_llm_signals("I could set that up.\nJon", strict=False, recipient='gail')
        self.assertFalse(any(c == 'promises_future_work' for c, _ in res['matches']))

    def test_quoted_lines_skipped(self):
        self.assertEqual(v.promises_future_work("> I'll send it soon, if you want.\nDone.\nJon"), [])

    def test_questions_to_reader_skipped(self):
        self.assertEqual(v.promises_future_work("Can you send the file soon?\nCould you check?\nJon"), [])

    def test_offer_in_question_form_hits(self):
        self.assertTrue(v.promises_future_work("Want a plan to move Nextcloud to 35?\nJon"))

    def test_next_weeks_call_is_a_reference(self):
        self.assertEqual(v.promises_future_work("Can she join next week's call?\nI'd like her on next week's call.\nJon"), [])

    def test_his_verbatim_words_skipped(self):
        self.assertEqual(v.promises_future_work("Jon: I'll be there soon.\nThe fix is live."), [])
        self.assertEqual(v.promises_future_work('He wrote "I\'ll send it" and it arrived.\nJon'), [])

    def test_cant_is_not_an_offer(self):
        self.assertEqual(v.promises_future_work("I can't reach the server.\nJon"), [])

    def test_greg_reply_passes_strict(self):
        res = v.detect_llm_signals(GREG_REPLY, strict=True, recipient='greg', is_reply=True, html=GOOD_SHAPE)
        self.assertFalse(any(c == 'promises_future_work' for c, _ in res['matches']))
        self.assertEqual(res['verdict'], 'PASS', res['matches'])


class OpenWorkHold(unittest.TestCase):
    def test_each_phrase_hits(self):
        for s in ("Two items are still open.", "That is not yet built.", "Next up is the backup.",
                  "We plan a rebuild.", "It is on my list.", "I haven't finished the import.",
                  "I'll get to the cert."):
            self.assertTrue(v.open_work_hits(s + "\nJon"), s)

    def test_strict_holds_and_explains(self):
        res = v.detect_llm_signals("The cert fix is still open.\nJon", strict=True, recipient='gail')
        self.assertEqual(res['verdict'], 'FAIL')
        msgs = [m for c, m in res['matches'] if c == 'open_work']
        self.assertTrue(msgs and 'HOLD' in msgs[0] and 'Do it first' in msgs[0])

    def test_not_checked_without_strict(self):
        res = v.detect_llm_signals("Still open.\nWhat is the date?\nJon", strict=False, recipient='gail')
        self.assertFalse(any(c in ('open_work', 'question_to_reader') for c, _ in res['matches']))

    def test_quoted_and_his_words_skipped(self):
        self.assertEqual(v.open_work_hits("> It is still open.\nJon: not yet.\nDone."), [])

    def test_question_to_reader_holds(self):
        res = v.detect_llm_signals("Which version does the host run?\nJon", strict=True, recipient='gail')
        self.assertEqual(res['verdict'], 'FAIL')
        msgs = [m for c, m in res['matches'] if c == 'question_to_reader']
        self.assertTrue(msgs and 'research and decide it ourselves' in msgs[0])

    def test_only_they_know_clears_the_question(self):
        res = v.detect_llm_signals("What name should go on the invoice?\nJon", strict=True,
                                   recipient='gail', only_they_know=True)
        self.assertFalse(any(c == 'question_to_reader' for c, _ in res['matches']))

    def test_quoted_question_is_not_asked(self):
        res = v.detect_llm_signals("> Which version does the host run?\nVersion 35, checked today.\nJon",
                                   strict=True, recipient='gail')
        self.assertFalse(any(c == 'question_to_reader' for c, _ in res['matches']))


# Synthetic mail only: no real client text lives in this file.
OLD_SHAPE = ('<div dir="auto">Pat,<br/><br/>One reply.<br/><br/>Widget repairs<br/><br/>'
             '- Fixed the first widget.<br/>- Fixed the second widget.<br/><br/>'
             'Gadget parts<br/><br/>The notes page is at<br/>wiki.example.digital/f/12<br/><br/>Jon</div>')
GOOD_SHAPE = ('<p>Pat,</p><p>One reply.</p><h2>Widget repairs</h2><ul><li>Fixed the first widget.</li>'
              '<li>Fixed the second widget.</li></ul><h2>Gadget parts</h2><p>The notes page:</p>'
              '<p><b><a href="https://wiki.example.digital/f/12">Gadget notes</a></b></p><p>Jon</p>')
QUOTE_TAIL = ('<br><div class="gmail_extra"><div>On Wed, Oct 21, 2026 12:44 PM, Pat &lt;pat@example.digital&gt; wrote:<br>'
              '<blockquote class="gmail_quote">%s</blockquote></div></div>')
OLD_QUOTED = ('<p>- a typed bullet</p><p>**bold**</p><p># heading</p><p>See https://example.com/a and wiki.example.digital/x</p>'
              '<p>Alpha topic here</p><p>Beta topic here</p>')


def fmt_cats(html, recipient='greg'):
    res = v.detect_llm_signals("Pat,\nBody.\nJon", recipient=recipient, html=html)
    return [c for c, _ in res['house'] if c.startswith('format_')], res


class CoopHtmlFormat(unittest.TestCase):
    def test_old_shape_fails_and_names_the_fix(self):
        cats, res = fmt_cats(OLD_SHAPE)
        self.assertEqual(res['verdict'], 'FAIL')
        self.assertEqual(sorted(cats), ['format_bullets', 'format_headings', 'format_links'])
        text = ' '.join(m for _, m in res['house'])
        self.assertIn('<ul><li>', text)
        self.assertIn('<h2>', text)
        self.assertIn('<b><a href=', text)

    def test_devops_list_is_gated_too(self):
        cats, _ = fmt_cats(OLD_SHAPE, recipient='devops')
        self.assertIn('format_bullets', cats)

    def test_good_17_oct_shape_passes(self):
        cats, _ = fmt_cats(GOOD_SHAPE)
        self.assertEqual(cats, [])

    def test_markdown_symbols_fail(self):
        cats, _ = fmt_cats('<p>Pat,</p><p>**Done** today.</p><p># Notes</p><p>Jon</p>')
        self.assertIn('format_markdown', cats)

    def test_non_bold_anchor_fails(self):
        cats, res = fmt_cats('<p>Pat,</p><p>See <a href="https://example.com/a">the page</a>.</p><p>Jon</p>')
        self.assertIn('format_links', cats)
        self.assertIn('not bold', ' '.join(m for _, m in res['house']))

    def test_bold_anchor_passes_either_nesting(self):
        for body in ('<b><a href="https://wiki.example.digital/f/12">Notes</a></b>',
                     '<a href="https://wiki.example.digital/f/12"><strong>Notes</strong></a>'):
            cats, _ = fmt_cats('<p>Pat,</p><p>%s</p><p>Jon</p>' % body)
            self.assertEqual(cats, [], body)

    def test_bare_address_fails_even_in_bold(self):
        for body in ('wiki.example.digital/f/12', '<b>wiki.example.digital/f/12</b>', 'https://example.com/a'):
            cats, res = fmt_cats('<p>Pat,</p><p>See %s</p><p>Jon</p>' % body)
            self.assertIn('format_links', cats, body)
            self.assertIn('<b><a href=', ' '.join(m for _, m in res['house']))

    def test_anchor_without_https_href_fails(self):
        for href in ('wiki.example.digital/f/12', 'javascript:void(0);', '//wiki.example.digital', '#', ''):
            cats, res = fmt_cats('<p>Pat,</p><p><b><a href="%s">Notes</a></b></p><p>Jon</p>' % href)
            self.assertIn('format_links', cats, href)
            self.assertIn('https://', ' '.join(m for _, m in res['house']))

    def test_email_addresses_are_not_links_to_make(self):
        cats, _ = fmt_cats('<p>Pat,</p><p>Write to pat@example.digital or <a href="mailto:pat@example.digital">her</a>.</p><p>Jon</p>')
        self.assertEqual(cats, [])

    def test_quoted_old_format_below_wrote_line_passes(self):
        cats, _ = fmt_cats(GOOD_SHAPE + '<br><div>On Wed, Oct 21, 2026 12:44 PM, Pat wrote:</div>' + OLD_QUOTED)
        self.assertEqual(cats, [])

    def test_quoted_old_format_in_blockquote_passes(self):
        cats, _ = fmt_cats(GOOD_SHAPE + QUOTE_TAIL % OLD_QUOTED)
        self.assertEqual(cats, [])

    def test_gt_quoted_points_pass(self):
        cats, _ = fmt_cats('<p>Pat,</p><p>&gt; - old bullet</p><p>&gt; see https://example.com</p><p>Yes.</p><p>Jon</p>')
        self.assertEqual(cats, [])

    def test_single_title_line_is_not_a_sectioned_letter(self):
        cats, _ = fmt_cats('<p>Pat,</p><p>Widget repairs</p><p>All fixed.</p><p>Jon</p>')
        self.assertEqual(cats, [])

    def test_other_recipients_get_only_the_link_rule(self):
        for r in ('william', 'gail', 'schlajo'):
            cats, res = fmt_cats(OLD_SHAPE, recipient=r)
            self.assertEqual(cats, ['format_links'], r)         # bare address; typed bullets are fine here
            self.assertFalse(any(c == 'format_unchecked' for c, _ in res['warnings']), r)
            cats, _ = fmt_cats(GOOD_SHAPE, recipient=r)
            self.assertEqual(cats, [], r)

    def test_other_recipients_quote_is_excluded_from_links(self):
        cats, _ = fmt_cats(GOOD_SHAPE + QUOTE_TAIL % OLD_QUOTED, recipient='gail')
        self.assertEqual(cats, [])

    def test_ticket_in_words_fails_for_everyone(self):
        body = '<p>Pat,</p><p>Both are on the list, as cd-nextcloud issue 18 and PR 24.</p><p>Jon</p>'
        for r in ('greg', 'gail', 'william'):
            cats, res = fmt_cats(body, recipient=r)
            self.assertIn('format_links', cats, r)
            text = ' '.join(m for _, m in res['house'])
            self.assertIn('2 ticket reference(s)', text, r)
            self.assertIn('issue 18', text, r)
        for ref in ('Deck card 16', '#312', 'owner/repo#12', 'pull request 9'):
            cats, _ = fmt_cats('<p>Pat,</p><p>See %s today.</p><p>Jon</p>' % ref, recipient='gail')
            self.assertIn('format_links', cats, ref)

    def test_linked_or_quoted_ticket_passes(self):
        linked = '<p>Pat,</p><p>See <b><a href="https://github.com/o/r/issues/18">cd-nextcloud issue 18</a></b>.</p><p>Jon</p>'
        self.assertEqual(fmt_cats(linked, recipient='gail')[0], [])
        quoted = '<p>Pat,</p><blockquote>is issue 18 done?</blockquote><p>Yes, all of it.</p><p>Jon</p>'
        self.assertEqual(fmt_cats(quoted, recipient='gail')[0], [])
        for words in ('three issues', 'page 2 of 3', 'it costs $12'):
            cats, _ = fmt_cats('<p>Pat,</p><p>There are %s left.</p><p>Jon</p>' % words, recipient='gail')
            self.assertEqual(cats, [], words)

    def test_many_topics_without_headings_fail(self):
        pairs = ''.join('<blockquote>point %d?</blockquote><p>Answer %d is here.</p>' % (i, i) for i in range(3))
        cats, res = fmt_cats('<p>Greg,</p>' + pairs + '<p>Jonathan</p>')
        self.assertIn('format_headings', cats)
        self.assertIn('<h2>', ' '.join(m for _, m in res['house']))
        wall = '<p>Greg,</p>' + ''.join('<p>Paragraph %d says a thing.</p>' % i for i in range(8)) + '<p>Jon</p>'
        self.assertIn('format_headings', fmt_cats(wall)[0])
        self.assertNotIn('format_headings', fmt_cats('<p>Greg,</p>' + pairs + '<p>Jonathan</p>', recipient='gail')[0])

    def test_many_topics_with_headings_or_list_pass(self):
        headed = '<p>Greg,</p>' + ''.join('<h2>Topic %d</h2><blockquote>point %d?</blockquote><p>Answer %d.</p>'
                                          % (i, i, i) for i in range(3)) + '<p>Jonathan</p>'
        self.assertEqual(fmt_cats(headed)[0], [])
        short = '<p>Greg,</p><blockquote>one?</blockquote><p>Yes.</p><blockquote>two?</blockquote><p>No.</p><p>Jon</p>'
        self.assertEqual(fmt_cats(short)[0], [])
        self.assertEqual(fmt_cats(GOOD_SHAPE + QUOTE_TAIL % ('<blockquote>a</blockquote>' * 4))[0], [])

    def test_plain_text_without_html_is_unchanged(self):
        res = v.detect_llm_signals("Pat,\n- a typed bullet\nJon", recipient='gail')
        self.assertFalse([c for c, _ in res['house'] if c.startswith('format_')])

    def test_greg_without_html_warns_not_fails(self):
        res = v.detect_llm_signals("Pat,\nBody.\nJon", recipient='greg')
        self.assertTrue(any(c == 'format_unchecked' for c, _ in res['warnings']))
        self.assertFalse([c for c, _ in res['house'] if c.startswith('format_')])

    def test_greg_without_html_fails_under_strict_and_names_the_flag(self):
        for r in ('greg', 'devops'):
            res = v.detect_llm_signals("Pat,\nBody.\nJon", recipient=r, strict=True)
            msgs = [m for c, m in res['house'] if c == 'format_unchecked']
            self.assertEqual(len(msgs), 1, r)
            self.assertIn('--html', msgs[0])
            self.assertEqual(res['verdict'], 'FAIL', r)
            self.assertFalse(any(c == 'format_unchecked' for c, _ in res['warnings']), r)

    def test_strict_other_recipients_need_no_html(self):
        res = v.detect_llm_signals("Pat,\nBody.\nJon", recipient='gail', strict=True)
        self.assertFalse([c for c, _ in res['house'] if c == 'format_unchecked'])


class RecordClaimsAndFlatAnswers(unittest.TestCase):
    def check(self, text, html=None, strict=False):
        return v.detect_llm_signals(text, recipient='reader', strict=strict, html=html)

    def test_record_claim_without_link_warns_and_strict_fails(self):
        body = '<blockquote class="gmail_quote"><p>Who decided?</p></blockquote><p>I recorded it.</p>'
        text = 'I recorded it.'
        warning = self.check(text, html=body)
        self.assertTrue(any(c == 'record_claim' and 'link' in m for c, m in warning['warnings']))
        strict = self.check(text, html=body, strict=True)
        self.assertEqual(strict['verdict'], 'FAIL')
        self.assertTrue(any(c == 'record_claim' for c, _ in strict['house']))

    def test_record_claim_passes_with_link_in_same_paragraph(self):
        body = ('<blockquote><p>Who decided?</p></blockquote>'
                '<p>I recorded it in <a href="https://example.test/notes">the notes</a>.</p>')
        res = self.check('I recorded it in the notes.', html=body, strict=True)
        self.assertFalse(any(c == 'record_claim' for c, _ in res['house']))

    def test_record_claim_needs_link_in_same_paragraph_or_list_item(self):
        separate_paragraphs = ('<p>I logged it.</p>'
                               '<p><a href="https://example.test/notes">The notes</a></p>')
        res = self.check('I logged it.', html=separate_paragraphs, strict=True)
        self.assertTrue(any(c == 'record_claim' for c, _ in res['house']))

        same_list_item = ('<ul><li><p>I logged it.</p>'
                          '<p><a href="https://example.test/notes">The notes</a></p></li></ul>')
        res = self.check('I logged it.', html=same_list_item, strict=True)
        self.assertFalse(any(c == 'record_claim' for c, _ in res['house']))

        same_paragraph = ('<p>I logged it.<br/>'
                          '<a href="https://example.test/notes">The notes</a></p>')
        res = self.check('I logged it.', html=same_paragraph, strict=True)
        self.assertFalse(any(c == 'record_claim' for c, _ in res['house']))

    def test_plain_text_record_claims_warn_with_url_instruction(self):
        for sentence in ("I've recorded it.", "I noted it.", "I wrote it up.", "It's on the list.",
                         "I filed it.", "I logged it.", "I tracked it.", "I tracked in the task list.",
                         "I put the task on a list."):
            res = self.check(sentence)
            messages = [m for c, m in res['warnings'] if c == 'record_claim']
            self.assertTrue(messages and 'add the URL' in messages[0], sentence)
            strict = self.check(sentence, strict=True)
            self.assertTrue(any(c == 'record_claim' for c, _ in strict['house']), sentence)

    def test_recorded_call_and_video_are_not_record_claims(self):
        for text in ("The recorded call and recorded video are in the folder.",
                     "I recorded a video of the demo."):
            self.assertFalse(v.record_claim_messages(text), text)
            self.assertFalse(v.record_claim_messages(text, html='<p>%s</p>' % text), text)
            self.assertFalse(any(c == 'record_claim' for c, _ in self.check(text, strict=True)['house']), text)

    def test_bare_answer_after_quote_warns_and_strict_fails(self):
        for answer in ("No.", "Yes.", "Nope.", "Yep."):
            html = ('<blockquote class="gmail_quote"><p>Is the work ready?</p></blockquote>'
                    '<p>%s The next step is clear.</p>' % answer)
            warning = self.check("I checked the work.", html=html)
            self.assertTrue(any(c == 'flat_answer' for c, _ in warning['warnings']), answer)
            flat_messages = [m for c, m in warning['warnings'] if c == 'flat_answer']
            self.assertIn('It\'s prepared but not running: <what proves it>', flat_messages[0])
            self.assertNotIn('Not yet', flat_messages[0])
            strict = self.check("I checked the work.", html=html, strict=True)
            self.assertEqual(strict['verdict'], 'FAIL', answer)
            self.assertTrue(any(c == 'flat_answer' for c, _ in strict['house']), answer)

    def test_prepared_state_passes_flat_answer_and_open_work(self):
        answer = "It's prepared but not running: I checked production at 9 AM."
        html = ('<blockquote class="gmail_quote"><p>Is the work running?</p></blockquote>'
                '<p>%s</p>' % answer)
        res = self.check(answer, html=html, strict=True)
        self.assertFalse(any(c in ('flat_answer', 'open_work') for c, _ in res['matches']),
                         res['matches'])
        self.assertFalse(any(c in ('flat_answer', 'open_work') for c, _ in res['house']),
                         res['house'])

    def test_not_a_flat_answer(self):
        for answer in ("No problem.", "No, it is still being prepared.",
                       "Yes, the setup is ready.", "Not yet. It is prepared but not running."):
            html = ('<blockquote><p>Is the work ready?</p></blockquote><p>%s</p>' % answer)
            res = self.check("I checked the work.", html=html, strict=True)
            self.assertFalse(any(c == 'flat_answer' for c, _ in res['house']), answer)

    def test_flat_answer_requires_a_preceding_blockquote(self):
        res = self.check("I checked the work.", html='<p>No.</p>', strict=True)
        self.assertFalse(any(c == 'flat_answer' for c, _ in res['house']))


class ThirdPersonSelf(unittest.TestCase):
    def test_name_in_prose_fails(self):
        self.assertTrue(v.third_person_self('Jon has the password.\nThanks\nJon'))

    def test_name_inside_a_url_passes(self):
        t = 'See https://github.com/TortoiseWolfe/RescueDogs/issues/495 for the fix.\nThanks, Jonathan'
        self.assertEqual(v.third_person_self(t), [])

    def test_name_beside_a_url_still_fails(self):
        t = 'TortoiseWolfe fixed https://github.com/TortoiseWolfe/RescueDogs/issues/1 today.\nThanks'
        self.assertTrue(v.third_person_self(t))

class UnexplainedAcronyms(unittest.TestCase):
    def names(self, t):
        return [a for a, _ in v.find_unexplained_acronyms(t)]

    def test_bare_acronym_flagged(self):
        self.assertEqual(self.names('The CSS is broken.'), ['CSS'])

    def test_explained_in_parentheses_passes(self):
        t = 'CSS (Cascading Style Sheets, the code that controls how a page looks) is broken.'
        self.assertEqual(self.names(t), [])

    def test_spelled_out_then_acronym_passes(self):
        t = 'Cascading Style Sheets (CSS) are broken. Later CSS is fixed.'
        self.assertEqual(self.names(t), [])

    def test_spelled_out_with_little_words_passes(self):
        self.assertEqual(self.names('The Department of Motor Vehicles (DMV) called.'), [])

    def test_short_for_passes(self):
        self.assertEqual(self.names('The CSS, short for Cascading Style Sheets, is broken.'), [])

    def test_issue_number_in_parentheses_is_not_an_explanation(self):
        self.assertEqual(self.names('The PR (#776) is up.'), ['PR'])

    def test_only_first_use_is_judged(self):
        t = 'The CSS is broken. CSS (Cascading Style Sheets, page looks) again.'
        self.assertEqual(self.names(t), ['CSS'])

    def test_plural_counts_as_the_acronym(self):
        self.assertEqual(self.names('Two PRs are up.'), ['PR'])

    def test_allow_list_passes(self):
        t = 'Call at 5 PM ET, OK? Bring your ID and PC. Chattanooga, TN and the US.'
        self.assertEqual(self.names(t), [])

    def test_capitals_inside_a_url_pass(self):
        t = 'See https://github.com/TortoiseWolfe/RescueDogs/pull/PR12 and http://x.com/CSS.'
        self.assertEqual(self.names(t), [])

    def test_email_address_code_and_tags_pass(self):
        t = 'Write to CEO@example.com. Run `npm CI` or <code>API</code>. [CC>MUSE] note.'
        self.assertEqual(self.names(t), [])

    def test_quoted_reply_lines_skipped(self):
        self.assertEqual(self.names('> The API is down.\nThanks.'), [])

    def test_html_text_is_read_as_prose(self):
        self.assertEqual(self.names('<p>The <b>DNS</b> moved.</p><pre>SQL ROWS</pre>'), ['DNS'])

    def test_capital_run_joined_to_a_name_is_skipped(self):
        for t in ('The account EBU-design is invited.', 'Sign in as TTW_admin.', 'Open EBU.config today.',
                  'Use my-EBU or ACE-Step 1.5.', 'The file is DATA_export.'):
            self.assertEqual(self.names(t), [], t)

    def test_hyphen_or_dot_at_a_sentence_edge_still_flags(self):
        self.assertEqual(self.names('We use CSS. It is fine.'), ['CSS'])
        self.assertEqual(self.names('We use CSS - it is fine.'), ['CSS'])

    def test_size_after_its_number_passes(self):
        self.assertEqual(self.names('The copy is 2.1 GB.'), [])
        self.assertEqual(self.names('Plenty of GB left.'), ['GB'])

    def test_shouted_plain_word_passes(self):
        self.assertEqual(self.names('This is NOT ready.'), [])

    def test_normal_mode_warns_strict_fails(self):
        t = 'The CSS is broken.\nJon'
        loose = v.detect_llm_signals(t, recipient='gail')
        self.assertTrue(any(c == 'acronym' for c, _ in loose['warnings']))
        self.assertFalse(any(c == 'acronym' for c, _ in loose['house']))
        self.assertNotEqual(loose['verdict'], 'FAIL')
        hard = v.detect_llm_signals(t, strict=True, recipient='gail')
        self.assertTrue(any(c == 'acronym' for c, _ in hard['house']))
        self.assertEqual(hard['verdict'], 'FAIL')

def team(text, **kw):
    return v.detect_llm_signals(text, recipient='team', **kw)


def has_cat(res, cat):
    return any(c == cat for c, _ in res['house'])


class BareRefs(unittest.TestCase):
    OK_LINK = "[the shop price list change (#274)](https://github.com/o/r/pull/274)"

    def test_bare_in_sentence_fails(self):
        res = team("The price moved in #274 last week.\nJon")
        self.assertTrue(has_cat(res, 'bare_ref'))
        self.assertEqual(res['verdict'], 'FAIL')

    def test_message_names_ref_and_quotes_context(self):
        msg = v.bare_ref_matches("The price moved in #274 last week.")[0]
        self.assertIn('#274', msg)
        self.assertIn('price moved in #274 last week', msg)

    def test_number_only_link_fails(self):
        for t in ("See [#274](https://github.com/o/r/pull/274).",
                  "See [PR #274](https://github.com/o/r/pull/274).",
                  "See [change #274](https://github.com/o/r/pull/274)."):
            self.assertTrue(has_cat(team(t + "\nJon"), 'bare_ref'), t)

    def test_link_with_words_passes(self):
        res = team("The price moved in " + self.OK_LINK + ".\nJon")
        self.assertFalse(has_cat(res, 'bare_ref'))
        self.assertEqual(v.bare_ref_matches("See " + self.OK_LINK), [])
        self.assertEqual(v.bare_ref_matches(
            "[the castle background ticket (#151)](https://github.com/o/r/issues/151)"), [])

    def test_forms_of_reference(self):
        for t in ("PR #12 landed.", "pull request #12 landed.", "issue #12 is open.",
                  "owner/repo#12 is open."):
            self.assertEqual(len(v.bare_ref_matches(t)), 1, t)

    def test_code_is_exempt(self):
        self.assertEqual(v.bare_ref_matches("Run `git show #274` now."), [])
        self.assertEqual(v.bare_ref_matches("Run:\n```\ngit show #274\n```\nDone."), [])

    def test_url_anchor_is_not_a_ref(self):
        self.assertEqual(v.bare_ref_matches("Open https://example.com/page#12 now."), [])

    def test_default_recipient_stays_silent(self):
        res = v.detect_llm_signals("The price moved in #274 last week.\nJon", recipient='gail')
        self.assertFalse(has_cat(res, 'bare_ref'))
        res = v.detect_llm_signals("The price moved in #274 last week.\nJon")
        self.assertFalse(has_cat(res, 'bare_ref'))

    def test_team_jargon_hard_fails_unless_glossed(self):
        self.assertTrue(has_cat(team("The repo moved.\nJon"), 'jargon'))
        self.assertFalse(has_cat(team("The repo moved, which means the code folder.\nJon"), 'jargon'))

    def test_team_has_no_word_limit(self):
        res = team(("Short plain words here. " * 100) + "\nJon")
        self.assertFalse(has_cat(res, 'length'))


class FactKeeper(unittest.TestCase):
    ORIG = ("The price lives in `src/lib/cosmetics.ts` and in apps/api/src/lib/prices.ts.\n"
            "Details: https://example.com/docs/prices. Merged by #274 on 29 Oct at 7:48 PM.\n"
            "The demo is 42s long and 2.1 GB, costs $5, and saved 30%.\n"
            "```\ngit log --oneline\n```\n- [x] done\n- [ ] todo\nJon")

    def problems(self, new):
        return v.dropped_facts(self.ORIG, new)

    def test_same_text_passes(self):
        self.assertEqual(self.problems(self.ORIG), [])

    def test_dropped_path_fails(self):
        p = self.problems(self.ORIG.replace('apps/api/src/lib/prices.ts', 'the price file'))
        self.assertTrue(any('apps/api/src/lib/prices.ts' in x for x in p), p)

    def test_dropped_url_fails(self):
        p = self.problems(self.ORIG.replace('https://example.com/docs/prices', 'the docs'))
        self.assertTrue(any('https://example.com/docs/prices' in x for x in p), p)

    def test_added_mention_fails(self):
        p = self.problems(self.ORIG + "\nThanks @someone")
        self.assertTrue(any('@someone' in x for x in p), p)

    def test_mention_already_there_passes(self):
        orig = "Thanks @someone for the help."
        self.assertEqual(v.dropped_facts(orig, "Many thanks to @someone."), [])

    def test_everything_moved_into_details_passes(self):
        new = ("Plain opening sentences.\n\n<details>\n<summary>Technical details</summary>\n\n"
               + self.ORIG.replace('Jon', '') + "\n</details>\n")
        self.assertEqual(self.problems(new), [])

    def test_bare_ref_turned_into_link_passes(self):
        orig = "It landed in #274."
        new = "It landed in [words here (#274)](https://github.com/o/r/pull/274)."
        self.assertEqual(v.dropped_facts(orig, new), [])

    def test_ref_kept_by_issue_url_only(self):
        self.assertEqual(v.dropped_facts("See #12.", "See [the ticket](https://github.com/o/r/issues/12)."), [])
        self.assertTrue(v.dropped_facts("See #12.", "See the ticket."))

    def test_dropped_ref_number_unit_date_code_fail(self):
        for old, new in (("See #12.", "See it."), ("It took 42s.", "It took a while."),
                         ("Seen 29 Oct.", "Seen then."), ("Seen at 7:48 PM.", "Seen late."),
                         ("Run `make test`.", "Run the tests."),
                         ("It cost $5.", "It cost little.")):
            self.assertTrue(v.dropped_facts(old, new), old)

    def test_case_ignored_except_code(self):
        self.assertEqual(v.dropped_facts("It is 2.1 GB, 29 Oct.", "it is 2.1 gb, 29 OCT."), [])
        self.assertTrue(v.dropped_facts("Run `Make Test`.", "Run `make test`."))

    def test_unequal_checkbox_counts_fail(self):
        p = self.problems(self.ORIG.replace('- [ ] todo', '- [x] todo'))
        self.assertTrue(any('checkbox' in x for x in p), p)

    def test_main_prints_facts_dropped_and_fails(self):
        import subprocess, tempfile
        here = Path(__file__).parent / 'email_voice_check.py'
        with tempfile.TemporaryDirectory() as d:
            orig = Path(d) / 'orig.md'
            orig.write_text("See https://example.com/a for it.\nJon")
            run = lambda body: subprocess.run(
                [sys.executable, str(here), '--stdin', '--strict', '--recipient', 'gail',
                 '--keep-facts-from', str(orig)], input=body, capture_output=True, text=True)
            ok = run("See https://example.com/a for it.\nJon")
            self.assertEqual(ok.returncode, 0, ok.stdout)
            bad = run("See the page for it.\nJon")
            self.assertEqual(bad.returncode, 2)
            self.assertIn('FACTS DROPPED:', bad.stdout)
            self.assertIn('https://example.com/a', bad.stdout)
            self.assertIn('Verdict: FAIL', bad.stdout)


class TeamRelaxedRules(unittest.TestCase):
    """29 Oct pilot: strict email-only rules misfire on ticket text."""
    def all_cats(self, res):
        return [c for c, _ in res['matches']] + [c for c, _ in res['house']]

    def check(self, text, recipient='team'):
        return v.detect_llm_signals(text, strict=True, recipient=recipient)

    def test_reflection_question_is_fine_for_team(self):
        t = "What did you learn in completing this ticket?\nThe price list is in one place now.\n"
        self.assertNotIn('question_to_reader', self.all_cats(self.check(t)))
        self.assertIn('question_to_reader', self.all_cats(self.check(t, 'jonathan')))

    def test_plans_are_fine_for_team(self):
        t = "I'll add the tests next. This is still open and not yet done; we plan to ship soon.\n"
        cats = self.all_cats(self.check(t))
        self.assertNotIn('promises_future_work', cats)
        self.assertNotIn('open_work', cats)
        self.assertIn('open_work', self.all_cats(self.check(t, 'jonathan')))

    def test_sender_we_is_fine_for_team(self):
        t = "We moved the shop onto one price list and our tests pass.\n"
        self.assertNotIn('sender_we', self.all_cats(self.check(t)))
        self.assertIn('sender_we', self.all_cats(self.check(t, 'jonathan')))

    def test_other_team_checks_still_run(self):
        res = self.check("The change is in #274 today.\n")
        self.assertIn('bare_ref', self.all_cats(res))
        self.assertIn('jargon', self.all_cats(self.check("The repo moved.\n")))

    def test_closing_lines_may_stay_bare(self):
        for line in ("Closes #274", "fixes #274", "RESOLVES #274", "- Closes #274", "Closes o/r#274",
                     "Closes #1, closes #2"):
            self.assertFalse(v.bare_ref_matches(line + "\n"), line)

    def test_closing_exemption_is_only_for_line_starts(self):
        self.assertTrue(v.bare_ref_matches("This also closes #274 for us.\n"))
        self.assertTrue(v.bare_ref_matches("Closes #274\nSee #275 for more.\n"))


class TeamQuotes(unittest.TestCase):
    def test_quoted_praise_word_passes(self):
        t = "I fixed the backup job.\n> It must be robust.\nJon"
        res = v.detect_llm_signals(t, recipient='team', strict=True)
        self.assertEqual(res['verdict'], 'PASS')
        self.assertIn('note: 1 quoted line not judged', res['skipped_note'])

    def test_unquoted_praise_word_fails(self):
        t = "The backup is robust.\nJon"
        res = v.detect_llm_signals(t, recipient='team', strict=True)
        self.assertEqual(res['verdict'], 'FAIL')
        self.assertTrue(any(c == 'generic_praise' for c, _ in res['matches']))

    def test_fenced_dashes_pass(self):
        t = "Here is the prompt:\n```\n- dashes - like - this -\n```\nJon"
        res = v.detect_llm_signals(t, recipient='team', strict=True)
        self.assertEqual(res['verdict'], 'PASS')
        self.assertIn('note: 1 code block not judged', res['skipped_note'])

    def test_inline_dash_passes(self):
        t = "Run `git log - oneline` now.\nJon"
        res = v.detect_llm_signals(t, recipient='team', strict=True)
        self.assertEqual(res['verdict'], 'PASS')

    def test_ordinary_dashes_fail(self):
        t = "The site is down - and I am sad - because of dashes - today.\nJon"
        res = v.detect_llm_signals(t, recipient='team', strict=True)
        self.assertEqual(res['verdict'], 'FAIL')
        self.assertTrue(any('mid-sentence dashes' in m for c, m in res['matches']))

    def test_quoted_bare_ref_passes(self):
        t = "I checked the notes.\n> see #77 for the old notes\nJon"
        res = v.detect_llm_signals(t, recipient='team', strict=True)
        self.assertEqual(res['verdict'], 'PASS')

    def test_unquoted_bare_ref_fails(self):
        t = "See #77 for the notes.\nJon"
        res = v.detect_llm_signals(t, recipient='team', strict=True)
        self.assertEqual(res['verdict'], 'FAIL')
        self.assertTrue(any(c == 'bare_ref' for c, _ in res['house']))

    def test_mostly_quoted_fails(self):
        t = "Notes:\n> 1\n> 2\n> 3\nJon"
        res = v.detect_llm_signals(t, recipient='team', strict=True)
        self.assertEqual(res['verdict'], 'FAIL')
        self.assertTrue(any(c == 'mostly_quoted' for c, _ in res['house']))

    def test_skipped_note_only_when_skipped(self):
        res = v.detect_llm_signals("Plain words.\nJon", recipient='team', strict=True)
        self.assertIsNone(res['skipped_note'])

    def test_fact_keeper_sees_inside_quote(self):
        orig = "See > https://example.com/a"
        new = "See > the page"
        problems = v.dropped_facts(orig, new)
        self.assertTrue(any('https://example.com/a' in p for p in problems))

    def test_default_and_greg_still_judge_quotes(self):
        t = "I fixed it.\n> It is robust.\nJon"
        # Default (greg)
        res_greg = v.detect_llm_signals(t, strict=True) # default recipient is greg
        self.assertEqual(res_greg['verdict'], 'FAIL')
        self.assertTrue(any(c == 'generic_praise' for c, _ in res_greg['matches']))
        
        # Explicit greg
        res_greg_explicit = v.detect_llm_signals(t, strict=True, recipient='greg')
        self.assertEqual(res_greg_explicit['verdict'], 'FAIL')
        
        # No recipient
        res_none = v.detect_llm_signals(t, strict=True, recipient=None)
        self.assertEqual(res_none['verdict'], 'FAIL')


class AccessAsk(unittest.TestCase):
    GAIL = ("Could you add jonpohlner@gmail.com as a Viewer in your Google Analytics so I can "
            "read the traffic numbers?\nJon")
    PROOF = "docs/access-inventory.md has no Analytics row; headless load of analytics.google.com shows no property"

    def run_cli(self, body, *extra):
        import subprocess
        return subprocess.run([sys.executable, str(Path(v.__file__)), '--stdin', *extra],
                              input=body, capture_output=True, text=True)

    def test_gail_sentence_fails_strict(self):
        res = v.detect_llm_signals(self.GAIL, strict=True, recipient='gail')
        self.assertEqual(res['verdict'], 'FAIL')
        self.assertTrue(any(c == 'access_ask' for c, _ in res['house']))

    def test_failure_names_the_sentence(self):
        out = self.run_cli(self.GAIL, '--strict', '--recipient', 'gail')
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('[access_ask]', out.stdout)
        self.assertIn('add jonpohlner@gmail.com as a Viewer', out.stdout)

    def test_proof_passes(self):
        res = v.detect_llm_signals(self.GAIL, strict=True, recipient='gail', access_proof=self.PROOF)
        self.assertFalse(any(c == 'access_ask' for c, _ in res['house']))
        self.assertIn(self.PROOF, res['access_note'])

    def test_cli_proof_flag_passes_the_gate(self):
        out = self.run_cli(self.GAIL, '--strict', '--recipient', 'gail', '--access-proof', self.PROOF)
        self.assertNotIn('[access_ask]', out.stdout)
        self.assertIn('allowed on proof', out.stdout)

    def test_cli_proof_flag_needs_a_value(self):
        out = self.run_cli(self.GAIL, '--strict', '--access-proof')
        self.assertEqual(out.returncode, 2)

    def test_one_word_proof_is_not_proof(self):
        res = v.detect_llm_signals(self.GAIL, strict=True, recipient='gail', access_proof='checked')
        self.assertTrue(any(c == 'access_ask' for c, _ in res['house']))

    def test_not_strict_only_warns(self):
        res = v.detect_llm_signals(self.GAIL, strict=False, recipient='gail')
        self.assertFalse(any(c == 'access_ask' for c, _ in res['house']))
        self.assertTrue(any(c == 'access_ask' for c, _ in res['warnings']))

    def test_other_asks_flag(self):
        for sentence in ("Please invite me to the workspace.",
                         "Can you give me access to the dashboard?",
                         "Grant access to the Search Console property.",
                         "Please share access with me.",
                         "Send me the password for the shop.",
                         "I need admin access to fix it.",
                         "Add me to the Slack channel.",
                         "I need a sign-in for the vendor portal.",
                         "Could you make Jon an Editor? Add him as an editor."):
            self.assertTrue(v.access_ask_hits(sentence + "\nJon"), sentence)

    def test_ordinary_sentences_do_not_flag(self):
        for sentence in ("You can view the report in the dashboard.",
                         "Sign in at app.harmonicegg.com/pricing to see the new prices.",
                         "The PDF viewer shows the invoice.",
                         "I added the new page to the menu.",
                         "I shared the report with Greg.",
                         "Your admin page lists every order.",
                         "Log in to see your orders.",
                         "I have access to the analytics already."):
            self.assertEqual(v.access_ask_hits(sentence + "\nJon"), [], sentence)

    def test_quoted_and_code_are_skipped(self):
        t = '> Could you add me as a Viewer?\nThanks for the note.\nJon'
        self.assertEqual(v.access_ask_hits(t), [])
        t = 'You wrote "invite me" in the form and I read it.\nJon'
        self.assertEqual(v.access_ask_hits(t), [])

    def test_team_text_exempt(self):
        res = v.detect_llm_signals("Please add me to the repo as a collaborator.", strict=True, recipient='team')
        self.assertFalse(any(c == 'access_ask' for c, _ in res['house'] + res['warnings']))


if __name__ == '__main__':
    unittest.main()
