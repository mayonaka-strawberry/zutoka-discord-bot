"""The IR catalog: one entry per effect id in the card DB (253 total).

Authored family-by-family from the old engine's per-card implementations
(zutomayo/effects/cards/effect_XX_YYY.py) — the behavioral ground truth.
Families follow the exploration catalog (A..AK). Sides: SELF=0, OPP=1.
"""

from __future__ import annotations

from ..cards import (
    ATTR_CHAOS, ATTR_DARKNESS, ATTR_ELECTRICITY, ATTR_FLAME, ATTR_WIND,
    SONG_NAMES, TYPE_AREA_ENCHANT, TYPE_CHARACTER, TYPE_ENCHANT,
)
from .ir import EffectIR, Sel, SELF, OPP

DARK, FLAME, ELEC, WIND, CHAOS = ATTR_DARKNESS, ATTR_FLAME, ATTR_ELECTRICITY, ATTR_WIND, ATTR_CHAOS


def SONG(name: str) -> int:
    return SONG_NAMES.index(name)


def E(effect_id: str, family: str, cond=None, ops=(), custom=None,
      inline=False, notes="") -> EffectIR:
    return EffectIR(effect_id, family, cond, tuple(ops), custom, inline, notes)


ENTRIES: list[EffectIR] = []


def _buff(effect_id: str, family: str, amount: int, cond=None, notes="") -> None:
    ENTRIES.append(E(effect_id, family, cond, (("atk_bonus", SELF, amount),), notes=notes))


# --- Family A: +attack if enemy battle character has attribute X -----------
_buff("01-025", "A", 50, ("enemy_attr", WIND))
_buff("01-027", "A", 50, ("enemy_attr", ELEC))
_buff("01-029", "A", 50, ("enemy_attr", DARK))
_buff("01-031", "A", 50, ("enemy_attr", FLAME))
_buff("01-054", "A", 30, ("enemy_attr", WIND))
_buff("01-056", "A", 30, ("enemy_attr", ELEC))
_buff("01-060", "A", 30, ("enemy_attr", DARK))
_buff("01-062", "A", 30, ("enemy_attr", FLAME))
_buff("01-091", "A", 20, ("or", ("enemy_attr", FLAME), ("enemy_attr", WIND)))
_buff("01-095", "A", 20, ("or", ("enemy_attr", DARK), ("enemy_attr", ELEC)))
_buff("02-009", "A", 20, ("enemy_attr", WIND))
_buff("02-021", "A", 20, ("enemy_attr", DARK))
_buff("02-069", "A", 20, ("enemy_attr", ELEC))
_buff("02-076", "A", 20, ("enemy_attr", FLAME))

# --- Family B: +attack by enemy battle character power cost ----------------
_buff("01-058", "B", 20, ("enemy_cost_ge", 2))
_buff("01-064", "B", 30, ("enemy_cost_ge", 2))
_buff("01-098", "B", 30, ("enemy_cost_le", 1))
_buff("01-101", "B", 20, ("enemy_cost_le", 1))
_buff("02-028", "B", 40, ("enemy_cost_eq_own",))
_buff("02-029", "B", 50, ("enemy_cost_ge", 6))

# --- Family C: +attack by time of day ---------------------------------------
_buff("01-053", "C", 30, ("time", "night"))
_buff("01-083", "C", 20, ("time", "night"))
_buff("02-025", "C", 50, ("time", "night"))
_buff("02-083", "C", 30, ("time", "night"))
_buff("03-053", "C", 40, ("time", "night"))
_buff("01-059", "C", 30, ("time", "day"))
_buff("01-102", "C", 20, ("time", "day"))
_buff("02-030", "C", 50, ("time", "day"))
_buff("02-095", "C", 30, ("time", "day"))
_buff("03-029", "C", 50, ("time", "day"))

# --- Family D: +attack when the turn crossed day/night ----------------------
# Old code compares chronos_at_turn_start's period vs the current period.
_buff("01-061", "D", 30, ("turn_became", "night"))
_buff("01-090", "D", 20, ("turn_became", "night"))
_buff("01-096", "D", 10, ("turn_became", "night"))
_buff("01-084", "D", 30, ("turn_became", "day"))
_buff("01-097", "D", 20, ("turn_became", "day"))

# --- Family E: +attack if own battle character has attribute X --------------
_buff("01-082", "E", 20, ("own_attr", DARK))
_buff("01-088", "E", 20, ("own_attr", FLAME))
_buff("01-094", "E", 20, ("own_attr", ELEC))
_buff("01-100", "E", 20, ("own_attr", WIND))
_buff("02-054", "E", 30, ("own_attr", FLAME))
_buff("02-057", "E", 30, ("own_attr", DARK))
_buff("02-060", "E", 30, ("own_attr", WIND))
_buff("02-063", "E", 30, ("own_attr", ELEC))
_buff("03-054", "E", 60, ("own_attr", DARK))
_buff("03-081", "E", 20, ("own_attr", FLAME))
_buff("03-089", "E", 50, ("own_attr", DARK))
_buff("03-090", "E", 30, ("own_attr", FLAME))
_buff("03-093", "E", 30, ("own_attr", WIND))
_buff("03-096", "E", 50, ("own_attr", ELEC))
_buff("03-099", "E", 30, ("own_attr", WIND))
_buff("03-100", "E", 40, ("own_attr", ELEC))

# --- Family G: unconditional +attack ----------------------------------------
_buff("01-030", "G", 30)
_buff("01-089", "G", 10)
_buff("02-032", "G", 30)
_buff("02-101", "G", 20)

# --- Family H: +attack if own HP at or below threshold ----------------------
_buff("01-032", "H", 50, ("own_hp_le", 30))
_buff("01-055", "H", 20, ("own_hp_le", 50))

# --- Family F: heal 10 if own battle character has attribute X --------------
for _eid, _attr in (("01-081", DARK), ("01-087", FLAME), ("01-093", ELEC), ("01-099", WIND),
                    ("02-085", DARK), ("02-091", FLAME), ("02-097", ELEC), ("02-103", WIND)):
    ENTRIES.append(E(_eid, "F", ("own_attr", _attr), (("heal", SELF, 10),)))

# --- Family I: static damage reduction ---------------------------------------
for _eid, _amount in (("01-028", 40), ("01-057", 30), ("01-085", 20),
                      ("02-089", 20), ("02-090", 30)):
    ENTRIES.append(E(_eid, "I", None, (("dmg_reduce", SELF, _amount),)))
# Neko-Reset-gated reductions (song check on own battle character)
ENTRIES.append(E("04-096", "I", ("battle_song", SELF, SONG("NEKO_RESET")),
                 (("dmg_reduce", SELF, 50),)))
ENTRIES.append(E("04-098", "I", ("battle_song", SELF, SONG("NEKO_RESET")),
                 (("dmg_reduce", SELF, 100),)))

# --- Family J: day/night attack reversal -------------------------------------
ENTRIES.append(E("01-005", "J", None, (("reverse_day_night", OPP),)))
ENTRIES.append(E("01-063", "J", None, (("reverse_day_night", SELF),)))

# --- Family O: +10 attack, override enemy battle character's attribute -------
# The +10 applies even when the enemy has no battle character (old code).
for _eid, _attr in (("02-084", ELEC), ("02-088", DARK), ("02-096", WIND), ("02-100", FLAME)):
    ENTRIES.append(E(_eid, "O", None,
                     (("atk_bonus", SELF, 10), ("attr_override_enemy", _attr))))

# --- Family V: +attack if enemy character's SEND TO POWER == 2 ---------------
_buff("04-060", "V", 20, ("enemy_stp_eq", 2))
_buff("04-066", "V", 30, ("enemy_stp_eq", 2))

# --- Family W: +attack if enemy character's attack is 0 ----------------------
# 04-034/04-039 honor the 04-099 attack override; 04-084/04-101 inline a
# computation that ignores it.
_buff("04-034", "W", 30, ("enemy_atk_eq0",))
_buff("04-039", "W", 40, ("enemy_atk_eq0",))
_buff("04-084", "W", 50, ("enemy_atk_eq0",))
_buff("04-101", "W", 20, ("enemy_atk_eq0",))

# --- Family X: +attack if opponent HP is exactly 100 -------------------------
_buff("03-006", "X", 100, ("opp_hp_eq", 100))
_buff("03-042", "X", 50, ("opp_hp_eq", 100))
_buff("03-087", "X", 40, ("opp_hp_eq", 100))
_buff("03-106", "X", 100, ("opp_hp_eq", 100))

# --- Family Y: +attack at (effective) midnight --------------------------------
_buff("03-001", "Y", 100, ("midnight",))
_buff("03-009", "Y", 50, ("midnight",))
_buff("03-025", "Y", 70, ("midnight",))

# --- Family Q: +attack if own charger contains >=1 card of attribute ---------
_buff("02-035", "Q", 20, ("charger_has_attr", SELF, FLAME))
_buff("02-040", "Q", 30, ("charger_has_attr", SELF, DARK))
_buff("02-045", "Q", 20, ("charger_has_attr", SELF, WIND))
_buff("02-049", "Q", 20, ("charger_has_attr", SELF, ELEC))

# --- Family P: +attack if charger non-empty and all one attribute ------------
for _eid, _amount, _attr in (
        ("02-053", 40, DARK), ("02-056", 30, FLAME), ("02-061", 40, ELEC), ("02-102", 50, WIND),
        ("03-019", 30, ELEC), ("03-028", 80, FLAME), ("03-036", 30, DARK), ("03-040", 20, FLAME),
        ("03-052", 40, WIND), ("03-082", 40, DARK), ("03-095", 50, ELEC), ("03-102", 40, WIND),
        ("04-014", 60, DARK), ("04-017", 50, FLAME), ("04-037", 20, DARK)):
    _buff(_eid, "P", _amount, ("charger_all_attr", SELF, _attr))
# Opponent's charger all one attribute
for _eid, _amount, _attr in (("03-011", 40, WIND), ("03-017", 40, DARK),
                             ("03-023", 40, FLAME), ("03-039", 50, ELEC)):
    _buff(_eid, "P", _amount, ("charger_all_attr", OPP, _attr))

# --- Family R: abyss-content conditions ---------------------------------------
for _eid, _amount, _attr in (
        ("02-081", 20, DARK), ("02-087", 20, FLAME), ("02-093", 20, ELEC), ("02-099", 30, WIND),
        ("03-060", 70, WIND), ("03-063", 60, ELEC), ("03-084", 70, FLAME), ("03-088", 30, DARK),
        ("04-064", 60, WIND), ("04-103", 40, WIND)):
    _buff(_eid, "R", _amount, ("abyss_all_attr", SELF, _attr))
_buff("03-030", "R", 70, ("abyss_attr_count_ge", SELF, ELEC, 3))
_buff("03-032", "R", 80, ("abyss_attr_count_ge", SELF, WIND, 3))
_buff("03-057", "R", 70, ("abyss_attr_count_ge", SELF, FLAME, 4))
_buff("03-083", "R", 60, ("abyss_attr_count_ge", SELF, DARK, 4))
# Per-card scaling
ENTRIES.append(E("04-005", "R", None,
                 (("atk_bonus", SELF, ("mul", ("count", Sel(SELF, "abyss", attribute=WIND)), 20)),)))
ENTRIES.append(E("04-020", "R", None,
                 (("atk_bonus", SELF, ("mul", ("count", Sel(SELF, "abyss", attribute=ELEC)), 20)),)))
# Distinct-attribute counts
_buff("01-007", "R", 50, ("abyss_distinct_attr_ge", SELF, 4))
_buff("03-008", "R", 100, ("abyss_distinct_attr_ge", SELF, 4))
_buff("03-022", "R", 40, ("abyss_distinct_attr_ge", SELF, 4))
_buff("03-051", "R", 40, ("abyss_distinct_attr_ge", SELF, 3))
_buff("03-062", "R", 50, ("abyss_distinct_attr_ge", SELF, 3))
# Sizes / emptiness
_buff("03-101", "R", 40, ("abyss_count_ge", SELF, 2))
_buff("04-011", "R", 50, ("abyss_empty", OPP))
_buff("04-029", "R", 100, ("abyss_empty", OPP))
_buff("04-056", "R", 50, ("abyss_empty", OPP))
_buff("04-030", "R", 40, ("abyss_empty", OPP))  # area enchant; removal in removal.py
# Abyss condition triggering another action
ENTRIES.append(E("02-014", "R", ("abyss_attr_count_ge", SELF, DARK, 2), (("heal", SELF, 20),)))
ENTRIES.append(E("02-050", "R", ("abyss_attr_count_ge", SELF, ELEC, 1), (("damage", OPP, 20),)))
ENTRIES.append(E("04-009", "R", ("abyss_count_ge", SELF, 3), (("atk_bonus", OPP, -30),)))

# --- Family Z: charger multi-attribute / song counts --------------------------
_buff("04-031", "Z", 100, ("charger_distinct_attr_ge", SELF, 4))
ENTRIES.append(E("04-059", "Z", ("charger_distinct_attr_ge", SELF, 3), (("heal", SELF, 50),)))
ENTRIES.append(E("04-093", "Z", ("charger_distinct_attr_ge", SELF, 2), (("heal", SELF, 30),)))
_buff("04-095", "Z", 50, ("charger_distinct_attr_ge", SELF, 4))  # area; removal on battle loss
ENTRIES.append(E("04-102", "Z", None,
                 (("atk_bonus", SELF, ("mul", ("count", Sel(SELF, "charger", song=SONG("STUDY_ME"))), 10)),)))
ENTRIES.append(E("04-104", "Z", None,
                 (("atk_bonus", SELF, ("mul", ("count", Sel(SELF, "charger", song=SONG("STUDY_ME"))), 20)),)))

# --- Family S: previous-turn battle character conditions ----------------------
_buff("02-010", "S", 20, ("prev_char_attr", SELF, FLAME))
_buff("02-018", "S", 20, ("prev_char_attr", SELF, WIND))
_buff("02-022", "S", 20, ("prev_char_attr", SELF, ELEC))
_buff("02-023", "S", 40, ("and", ("prev_char_attr", SELF, ELEC),
                          ("or", ("enemy_attr", FLAME), ("enemy_attr", DARK))))
_buff("02-042", "S", 20, ("prev_char_attr", SELF, DARK))
_buff("02-047", "S", 40, ("and", ("prev_char_attr", SELF, WIND), ("time", "day")))
_buff("02-068", "S", 30, ("and", ("prev_char_attr", SELF, FLAME), ("time", "night")))
# 02-041: prev dark -> route own deck top to charger/abyss by SEND TO POWER
ENTRIES.append(E("02-041", "S", ("prev_char_attr", SELF, DARK), (("deck_top_route", SELF),)))

# --- Family AI: own battle character cost / charger size ----------------------
_buff("02-092", "AI", 20, ("own_cost_ge", 2))   # area; removal: opp char cost >= 4
_buff("02-104", "AI", 20, ("own_cost_le", 2))   # area; removal: own char cost >= 4
_buff("03-091", "AI", 20, ("own_cost_ge", 3))   # area; removal: opp abyss placement
_buff("03-056", "AI", 50, ("charger_count_le", SELF, 3))

# --- Family AH: buff own character set this turn, by time of day --------------
_buff("02-026", "AH", 30, ("and", ("time", "night"), ("own_battle_played",)))
_buff("02-059", "AH", 30, ("and", ("time", "day"), ("own_battle_played",)))

# --- Family K: chronos manipulation --------------------------------------------
MIDNIGHT_POS, NOON_POS = 4, 13
ENTRIES.append(E("01-008", "K", None, (("chronos_revert_turn_start",),),
                 notes="raw assignment; no transition flags recorded (old code)"))
ENTRIES.append(E("01-026", "K", None, (("chronos_back_opp_clock",),)))
ENTRIES.append(E("02-036", "K", ("abyss_attr_count_ge", SELF, FLAME, 2),
                 (("set_chronos_to", MIDNIGHT_POS),)))
ENTRIES.append(E("02-106", "K", ("abyss_attr_count_ge", SELF, WIND, 2),
                 (("set_chronos_to", NOON_POS),)))
ENTRIES.append(E("03-005", "K", ("hp_lt_opp",), (("set_chronos_to", MIDNIGHT_POS),)))
ENTRIES.append(E("03-007", "K", ("hp_lt_opp",), (("set_chronos_to", NOON_POS),)))
ENTRIES.append(E("03-026", "K", None, (("midnight_extend",),)))
ENTRIES.append(E("03-033", "K", ("time", "day"), (("adv_chronos", 2),)))
# 02-011: if prev char flame, choose 0-5 clock advance
ENTRIES.append(E("02-011", "K", ("prev_char_attr", SELF, FLAME),
                 (("pick_number", 0, 0, 5), ("adv_chronos", ("reg", 0)))))
# 04-106: half of the 18-slot clock, so it always flips day/night exactly once.
ENTRIES.append(E("04-106", "K", None, (("adv_chronos", 9),)))

# --- Family L: draw / hand-cycling ----------------------------------------------
# 01-092 / 04-089: draw 1 + permanent pending hand bonus, both gated on can_draw
ENTRIES.append(E("01-092", "L", ("deck_ge", SELF, 1),
                 (("draw_exact", SELF, 1), ("hand_bonus", SELF))))
ENTRIES.append(E("04-089", "L",
                 ("and", ("battle_song", SELF, SONG("TAIDADA")), ("deck_ge", SELF, 1)),
                 (("draw_exact", SELF, 1), ("hand_bonus", SELF))))
# 02-027/02-031: two sequential picks -> deck bottom -> draw 2 (all-or-nothing)
for _eid in ("02-027", "02-031"):
    ENTRIES.append(E(_eid, "L", ("hand_count_ge", SELF, 2),
                     (("picks_exact", 0, Sel(SELF, "hand"), 2),
                      ("move_reg", 0, "deck", SELF, "bottom"),
                      ("draw_exact", SELF, 2))))
# 02-082/02-094: one pick -> deck bottom -> draw 1 (all-or-nothing)
for _eid in ("02-082", "02-094"):
    ENTRIES.append(E(_eid, "L", None,
                     (("pick_card", 0, Sel(SELF, "hand")),
                      ("move_reg", 0, "deck", SELF, "bottom"),
                      ("draw_exact", SELF, 1))))
# 04-061: choose N (number prompt then picks) -> deck bottom -> draw min(N, deck)
ENTRIES.append(E("04-061", "L", None,
                 (("multiselect", 0, Sel(SELF, "hand"), 0),
                  ("if_reg_empty", 0, 3),
                  ("move_reg", 0, "deck", SELF, "bottom"),
                  ("draw", SELF, ("reg_len", 0)))))
# 03-031: one pick (any hand card) -> abyss -> draw 1 (all-or-nothing)
ENTRIES.append(E("03-031", "L", None,
                 (("pick_card", 0, Sel(SELF, "hand")),
                  ("move_reg", 0, "abyss", SELF, "bottom"),
                  ("draw_exact", SELF, 1))))
# 04-054/04-058: one filtered pick -> abyss -> draw 1
ENTRIES.append(E("04-054", "L", None,
                 (("pick_card", 0, Sel(SELF, "hand", attribute=ELEC)),
                  ("move_reg", 0, "abyss", SELF, "bottom"),
                  ("draw_exact", SELF, 1))))
ENTRIES.append(E("04-058", "L", None,
                 (("pick_card", 0, Sel(SELF, "hand", attribute=WIND)),
                  ("move_reg", 0, "abyss", SELF, "bottom"),
                  ("draw_exact", SELF, 1))))
# 04-062/04-063: choose N filtered -> abyss -> draw min(N, deck)
ENTRIES.append(E("04-062", "L", None,
                 (("multiselect", 0, Sel(SELF, "hand", attribute=DARK), 0),
                  ("if_reg_empty", 0, 3),
                  ("move_reg", 0, "abyss", SELF, "bottom"),
                  ("draw", SELF, ("reg_len", 0)))))
ENTRIES.append(E("04-063", "L", None,
                 (("multiselect", 0, Sel(SELF, "hand", attribute=FLAME), 0),
                  ("if_reg_empty", 0, 3),
                  ("move_reg", 0, "abyss", SELF, "bottom"),
                  ("draw", SELF, ("reg_len", 0)))))
# 01-086: swap one hand card with one abyss card (both picked before moves)
ENTRIES.append(E("01-086", "L", ("and", ("hand_count_ge", SELF, 1), ("abyss_count_ge", SELF, 1)),
                 (("pick_card", 0, Sel(SELF, "hand")),
                  ("pick_card", 1, Sel(SELF, "abyss")),
                  ("move_reg", 0, "abyss", SELF, "bottom"),
                  ("move_reg", 1, "hand", SELF, "bottom"))))
# 04-053: 'may' place a STUDY-ME character from hand -> own charger -> draw 1.
# The pick is declinable (card text: "you may place... if you do, draw 1");
# skipping (or no candidate) jumps past both the move and the draw.
ENTRIES.append(E("04-053", "L", None,
                 (("pick_card_opt", 0, Sel(SELF, "hand", card_type=TYPE_CHARACTER, song=SONG("STUDY_ME")), 3),
                  ("move_reg", 0, "charger", SELF, "bottom"),
                  ("draw_exact", SELF, 1)),
                 notes="'may' place STUDY_ME char on charger; skipping declines both the move and the draw"))

# --- Family M: opponent deck/abyss disruption ------------------------------------
ENTRIES.append(E("01-104", "M", None, (("mill", OPP, 1),)))
ENTRIES.append(E("04-057", "M", ("abyss_count_ge", SELF, 3), (("mill", OPP, 2),)))
ENTRIES.append(E("01-103", "M", None,
                 (("pick_card", 0, Sel(OPP, "abyss")),
                  ("move_reg", 0, "deck", OPP, "bottom"))))
ENTRIES.append(E("04-090", "M", None,
                 (("pick_card", 0, Sel(OPP, "abyss")),
                  ("move_reg", 0, "deck", OPP, "bottom"))))

# --- Family T: remove opponent charger STP card to bottom of their deck ----------
ENTRIES.append(E("02-008", "T", ("own_attr", ELEC),
                 (("pick_card", 0, Sel(OPP, "charger", stp_eq=2)),
                  ("move_reg", 0, "deck", OPP, "bottom"))))
ENTRIES.append(E("02-019", "T", ("prev_char_attr", SELF, WIND),
                 (("pick_card", 0, Sel(OPP, "charger", stp_eq=1)),
                  ("move_reg", 0, "deck", OPP, "bottom"))))
ENTRIES.append(E("02-024", "T", ("and", ("prev_char_attr", SELF, ELEC), ("time", "night")),
                 (("pick_card", 0, Sel(OPP, "charger", stp_eq=1)),
                  ("move_reg", 0, "deck", OPP, "bottom"))))

# --- Family U: remove the opponent's area enchant ---------------------------------
ENTRIES.append(E("02-055", "U", ("own_attr", FLAME), (("bounce_opp_area", "top", True),)))
ENTRIES.append(E("03-014", "U", None, (("bounce_opp_area", "bottom", True),)))
ENTRIES.append(E("03-021", "U", None, (("bounce_opp_area", "top", True),)))
ENTRIES.append(E("04-107", "U", None, (("opp_area_to_abyss",),),
                 notes="to the abyss, forced even with SEND TO POWER (card text)"))

# --- 01-006: use an enchant effect from your own abyss (nested resolution) --------
ENTRIES.append(E("01-006", "L", None, custom="use_abyss_enchant",
                 notes="borrowed effect dispatched without cost check (old _dispatch)"))

# --- Family N: reveal / hand-information -------------------------------------------
# 03-045: reveal opponent's hand, then shuffle it (chance event; skipped on empty hand)
ENTRIES.append(E("03-045", "N", ("hand_count_ge", OPP, 1),
                 (("reveal_hand", OPP), ("shuffle_hand", OPP))))
# Reveal own hand, buff on distinct-attribute count (empty hand -> no effect)
_buff("04-008", "N", 80, ("and", ("hand_count_ge", SELF, 1), ("hand_distinct_attr_ge", SELF, 4)))
_buff("04-097", "N", 50, ("and", ("hand_count_ge", SELF, 1), ("hand_distinct_attr_ge", SELF, 3)))
_buff("04-032", "N", 50, ("and", ("hand_count_ge", SELF, 1), ("hand_distinct_attr_ge", SELF, 4)))
# Name-guess: SelectIdentity, then a 1-based number pick into the opponent's
# hand (old prompts: text input then number selection); +N attack on match.
for _eid, _amount in (("03-047", 50), ("03-059", 100), ("03-094", 40), ("03-105", 100)):
    ENTRIES.append(E(_eid, "N", ("hand_count_ge", OPP, 1),
                     (("name_guess", 0),
                      ("pick_number", 1, 1, ("hand_len", OPP)),
                      ("name_guess_bonus", 0, 1, _amount))))

# --- Family AA: TAIDADA reveal-count buffs ------------------------------------------
# multiselect = number prompt (0..len) then that many picks, matching both the
# old _prompt_card_multiselect cards and 04-035's hand-rolled version.
# The if_reg_empty guard falls through to the next op rather than skipping the
# reveal: reveal_reg emits an empty-tailed EVENT_CARDS_REVEALED that the
# narrator renders as "nothing revealed", and atk_bonus already adds
# 0 * _per == 0 on that path. The now-vestigial op is kept rather than deleted
# because features.py featurizes op verbs into EFFECT_FEATURES, a registered
# buffer in the deployed alpha_zero / ppo_transformer checkpoints; jump targets
# are not featurized, so retargeting it leaves those checkpoints untouched.
for _eid, _per in (("04-001", 30), ("04-007", 20), ("04-010", 20),
                   ("04-035", 10), ("04-091", 10)):
    ENTRIES.append(E(_eid, "AA", None,
                     (("multiselect", 0, Sel(SELF, "hand", card_type=TYPE_CHARACTER, song=SONG("TAIDADA")), 0),
                      ("if_reg_empty", 0, 2),
                      ("reveal_reg", 0),
                      ("atk_bonus", SELF, ("mul", ("reg_len", 0), _per)))))
ENTRIES.append(E("04-055", "AA", ("battle_song", SELF, SONG("TAIDADA")), (("heal", SELF, 20),)))

# --- Family AB: SHADE swap-synergy ---------------------------------------------------
ENTRIES.append(E("04-041", "AB", ("swapped_from_song", SONG("SHADE")),
                 (("negate_opp_set_enchants",),)))
ENTRIES.append(E("04-073", "AB", ("swapped_from_song", SONG("SHADE")), (("heal", SELF, 20),)))
ENTRIES.append(E("04-074", "AB", ("swapped_from_song", SONG("SHADE")), (("atk_bonus", OPP, -30),)))
ENTRIES.append(E("04-075", "AB", ("swapped_from_song", SONG("SHADE")), (("damage", OPP, 20),)))
ENTRIES.append(E("04-092", "AB", ("battle_song", SELF, SONG("SHADE")), (("atk_bonus", OPP, -40),)))
ENTRIES.append(E("04-094", "AB", None, custom="shade_use_one",
                 notes="area; removal at >=5 charger cards"))
ENTRIES.append(E("04-002", "AB", None, custom="shade_use_two"))

# --- Family AC: STUDY-ME swap buffs ---------------------------------------------------
_buff("04-023", "AC", 100, ("swapped_from_song", SONG("STUDY_ME")))
# 04-024: damage_not_reducible applies unconditionally; buff needs the swap
ENTRIES.append(E("04-024", "AC", None,
                 (("not_reducible", SELF),
                  ("if_not", ("swapped_from_song", SONG("STUDY_ME")), 3),
                  ("atk_bonus", SELF, 110))))
_buff("04-087", "AC", 50, ("swapped_from_song", SONG("STUDY_ME")))

# --- Family AD: Neko-Reset battle-zone conditionals ------------------------------------
ENTRIES.append(E("04-099", "AD", ("battle_song", SELF, SONG("NEKO_RESET")),
                 (("atk_override", OPP, 100),)))
ENTRIES.append(E("04-100", "AD", ("battle_song", SELF, SONG("NEKO_RESET")),
                 (("reflect", SELF),)))

# --- Family AE: end-of-turn effects -----------------------------------------------------
ENTRIES.append(E("03-027", "AE", None, (("heal", OPP, 50), ("eot_damage", OPP, 50))))
# 03-058 / 03-085: dispatchable no-ops — their healing/clock/removal behavior
# lives in turn_end.py and removal.py (mirrors the old engine layout).
ENTRIES.append(E("03-058", "AE", None, (), notes="behavior in process_end_of_turn_effects"))
ENTRIES.append(E("03-085", "AE", None, (), notes="behavior in process_end_of_turn_effects"))

# --- Family AF: CHAOS bank-or-lose bombs -------------------------------------------------
ENTRIES.append(E("04-006", "AF", None, custom="chaos_04_006"))
ENTRIES.append(E("04-027", "AF", None, (
    ("if_not", ("abyss_count_ge", SELF, 1), 7),   # no cards to bank -> lose
    ("pick_number", 0, 1, ("abyss_len", SELF)),
    ("picks_exact", 1, Sel(SELF, "abyss"), ("reg", 0)),
    ("shuffle_reg", 1),
    ("move_reg", 1, "deck", SELF, "bottom"),
    ("mill", OPP, ("reg", 0)),
    ("jump", 8),                                   # skip the lose branch
    ("lose_game",),
)))
ENTRIES.append(E("04-028", "AF", None, (
    ("if_not", ("abyss_count_ge", SELF, 6), 7),   # fewer than 6 -> lose
    ("picks_exact", 0, Sel(SELF, "abyss"), 6),
    ("shuffle_reg", 0),
    ("move_reg", 0, "deck", SELF, "bottom"),
    ("pick_chronos", 1),
    ("set_chronos_to", ("reg", 1)),
    ("jump", 8),
    ("lose_game",),
)))
ENTRIES.append(E("04-088", "AF", None, custom="chaos_04_088"))
# 04-105: bank 8, then BOTH chargers empty into their own owners' abysses.
# Falling short of 8 is an immediate self-defeat and nothing else resolves
# (confirmed ruling) — so the wipe lives only on the success branch.
ENTRIES.append(E("04-105", "AF", None, (
    ("if_not", ("abyss_count_ge", SELF, 8), 7),   # fewer than 8 -> lose, nothing else
    ("picks_exact", 0, Sel(SELF, "abyss"), 8),
    ("shuffle_reg", 0),
    ("move_reg", 0, "deck", SELF, "bottom"),
    ("charger_to_abyss", SELF),
    ("charger_to_abyss", OPP),
    ("jump", 8),                                   # skip the lose branch
    ("lose_game",),
), notes="8 own-abyss cards banked face down to deck bottom, then both power "
         "chargers empty into their own owners' abysses; short abyss = immediate "
         "self-defeat with no charger wipe"))

# --- Family AG: cost reduction / power generation -----------------------------------------
ENTRIES.append(E("02-006", "AG", None, (("cost_reduce_set_chars", 2),),
                 notes="forced-first (cost reducer)"))
ENTRIES.append(E("04-065", "AG", None, (("cost_reduce_battle_song", SONG("STUDY_ME"), 2),),
                 notes="forced-first (cost reducer); area, removal on non-STUDY-ME swap"))
ENTRIES.append(E("02-058", "AG", None,
                 (("power_bonus", SELF, ("count", Sel(SELF, "abyss", attribute=DARK))),),
                 notes="area; removal when own character placed on own charger"))

# --- Family AJ: reveal-top-of-opponent-deck area enchants -----------------------------------
ENTRIES.append(E("03-097", "AJ", None, custom="reveal_top_03_097"))
ENTRIES.append(E("03-103", "AJ", None, custom="reveal_top_03_103"))

# --- Remaining area enchants with per-turn resolve bodies -----------------------------------
ENTRIES.append(E("02-064", "AK", None,
                 (("atk_bonus", SELF, ("mul", ("count", Sel(SELF, "charger", attribute=ELEC)), 20)),),
                 notes="area; removal when opponent HP <= 30"))
_buff("02-086", "AK", 20, ("time", "night"))
_buff("02-098", "AK", 20, ("time", "day"))
# 03-055: bounce opponent's area to their deck bottom WITHOUT leave-play
# cleanup (old code inline), then block their area-enchant placement.
ENTRIES.append(E("03-055", "AK", None,
                 (("bounce_opp_area", "bottom", False), ("block_area", OPP))))
ENTRIES.append(E("03-064", "AK", None,
                 (("atk_bonus", SELF, ("hp", SELF)), ("atk_bonus", OPP, ("hp", OPP))),
                 notes="area; removal when opponent HP <= 40"))
for _eid, _attr in (("03-086", DARK), ("03-092", FLAME), ("03-098", ELEC), ("03-104", WIND)):
    ENTRIES.append(E(_eid, "AK", None,
                     (("atk_bonus", SELF, ("mul", ("count", Sel(SELF, "abyss", attribute=_attr)), 10)),),
                     notes="area; removal at >=4 abyss cards"))
_buff("04-033", "AK", 20, ("abyss_all_attr", SELF, WIND))
# 03-061: dispatchable no-op; clock override + removal live in the engine.
ENTRIES.append(E("03-061", "AK", None, (), notes="all clocks 1 via battle.all_clocks_one"))
# 02-015: additional enchant from hand + draw (custom; condition on entry)
ENTRIES.append(E("02-015", "S", ("and", ("prev_char_attr", SELF, DARK), ("time", "day")),
                 custom="additional_enchant_02_015"))

# --- Engine-inline passives (excluded from the old handler registry) ------------------------
ENTRIES.append(E("02-005", "AK", None, (), inline=True,
                 notes="disables opponent CHARACTER clocks; battle.opponent_clock_disabled"))
ENTRIES.append(E("02-007", "AK", None, (), inline=True,
                 notes="own attack always uses day value; battle.force_day_active"))
ENTRIES.append(E("02-062", "AK", None, (), inline=True,
                 notes="may skip character swap; game._skip_swap_prompt_needed"))
