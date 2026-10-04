"""Attack, battle resolution, win checks, and the passives read here
(02-005, 02-007, 03-061, 03-026, and the 01-005 flag)."""

from __future__ import annotations

from .cards import (
    ATK_DAY_T, ATK_NIGHT_T, POWER_COST_T, SEND_TO_POWER_T, EFFECT_T,
    EFFECT_TO_INDEX,
)
from .events import (
    EVENT_BATTLE_RESULT, EVENT_CHRONOS_ADVANCED, EVENT_CHRONOS_SET,
    EVENT_HP_CHANGED,
)
from .state import (
    GameState, PlayerState,
    PF_DAMAGE_REDUCTION, PF_DAY_NIGHT_REVERSED, PF_POWER_BONUS,
    PF_DAMAGE_NOT_REDUCIBLE, PF_BATTLE_DAMAGE,
    PF_DAMAGE_TAKEN, PF_BATTLE_LOST, PF_DAMAGE_REDUCED,
    GF_MIDNIGHT_EXTENDED, GF_DAY_TO_NIGHT, GF_NIGHT_TO_DAY,
    ATTACK_MOD_SET, ATTACK_MOD_ADD_OWN_HP,
)

CHRONOS_SIZE = 18
MIDNIGHT = 4
NIGHT_END = 8
NOON = 13

# Passive effects checked here (dense effect indices).
FX_01_005 = EFFECT_TO_INDEX["01-005"]  # reverse opponent's day/night (sets flag)
FX_02_005 = EFFECT_TO_INDEX["02-005"]  # area: disable opponent's CHARACTER clock
FX_02_007 = EFFECT_TO_INDEX["02-007"]  # area: own attack always uses day value
FX_03_061 = EFFECT_TO_INDEX["03-061"]  # area: all clocks count as 1
FX_03_026 = EFFECT_TO_INDEX["03-026"]  # midnight widened to +/-2 (sets gflag)


def total_power(state: GameState, player: PlayerState) -> int:
    return sum(SEND_TO_POWER_T[state.inst_def[i]] for i in player.charger)


def effective_power_cost(state: GameState, instance_id: int) -> int:
    cost = POWER_COST_T[state.inst_def[instance_id]] - state.inst_cost_red[instance_id]
    return cost if cost > 0 else 0


def area_enchant_active(state: GameState, player: PlayerState, effect_index: int) -> bool:
    """Whether `player`'s area enchant (set zone C) is `effect_index` with its cost met.
    Area enchants ignore the power bonus."""
    area = player.set_c
    if area == -1 or EFFECT_T[state.inst_def[area]] != effect_index:
        return False
    return total_power(state, player) >= effective_power_cost(state, area)


def force_day_active(state: GameState, player_index: int) -> bool:
    return area_enchant_active(state, state.players[player_index], FX_02_007)


def opponent_clock_disabled(state: GameState, player_index: int) -> bool:
    """02-005 owned by the opponent disables this player's CHARACTER clocks."""
    return area_enchant_active(state, state.players[1 - player_index], FX_02_005)


def all_clocks_one(state: GameState) -> bool:
    return (area_enchant_active(state, state.players[0], FX_03_061)
            or area_enchant_active(state, state.players[1], FX_03_061))


def is_effectively_midnight(state: GameState) -> bool:
    if state.chronos == MIDNIGHT:
        return True
    return bool(state.gflags[GF_MIDNIGHT_EXTENDED]) and abs(state.chronos - MIDNIGHT) <= 2


def set_chronos(state: GameState, new_value: int, *,
                record_transition: bool = True) -> None:
    """Set chronos, recording any day/night crossing. Pass `record_transition=False`
    for rewinds, which are not crossings (Q&A No.17)."""
    old_is_night = state.chronos <= NIGHT_END
    new_is_night = new_value <= NIGHT_END
    if record_transition:
        if old_is_night and not new_is_night:
            state.gflags[GF_NIGHT_TO_DAY] = 1
        elif not old_is_night and new_is_night:
            state.gflags[GF_DAY_TO_NIGHT] = 1
    state.chronos = new_value
    if state.event_sink is not None:
        state.event_sink.append((EVENT_CHRONOS_SET, new_value))


def advance_chronos_by(state: GameState, steps: int) -> None:
    """Advance chronos, recording every day/night crossing on the way."""
    chronos = state.chronos
    for _ in range(steps):
        old_is_night = chronos <= NIGHT_END
        chronos = (chronos + 1) % CHRONOS_SIZE
        new_is_night = chronos <= NIGHT_END
        if old_is_night and not new_is_night:
            state.gflags[GF_NIGHT_TO_DAY] = 1
        elif not old_is_night and new_is_night:
            state.gflags[GF_DAY_TO_NIGHT] = 1
    state.chronos = chronos
    if state.event_sink is not None and steps > 0:
        state.event_sink.append((EVENT_CHRONOS_ADVANCED, steps, chronos))


def base_attack(state: GameState, player: PlayerState) -> int:
    """Printed attack for the current half of the clock, after 02-007 and day/night
    reversal (01-005, 01-063). Ignores the power gate and modifiers."""
    def_index = state.inst_def[player.battle]
    if force_day_active(state, player.index):
        return ATK_DAY_T[def_index]
    if player.flags[PF_DAY_NIGHT_REVERSED]:
        return ATK_DAY_T[def_index] if state.is_night else ATK_NIGHT_T[def_index]
    return ATK_NIGHT_T[def_index] if state.is_night else ATK_DAY_T[def_index]


def get_effective_attack(state: GameState, player: PlayerState) -> int:
    """Final attack: this turn's modifiers folded onto the base in resolution order,
    clamped to >= 0 after each step (Q&A No.54, 68, 82).

    An unmet power cost makes it 0, even after a 04-099 set: Ground Rules 2.3.6 and
    5.1.3.2, Q&A No.73. (Not Ground Rules 7.1.2, which covers added attack only.)
    """
    if player.battle == -1:
        return 0

    cost = effective_power_cost(state, player.battle)
    if total_power(state, player) + player.flags[PF_POWER_BONUS] < cost:
        return 0

    attack = base_attack(state, player)
    modifiers = player.attack_mods
    for index in range(0, len(modifiers), 2):
        kind = modifiers[index]
        if kind == ATTACK_MOD_SET:
            attack = modifiers[index + 1]
        else:
            attack += player.hp if kind == ATTACK_MOD_ADD_OWN_HP else modifiers[index + 1]
            if attack < 0:
                attack = 0
    return attack


def record_hp_zero(state: GameState, player_index: int) -> None:
    """End the game the instant HP reaches 0 (Ground Rules 1.2.3, 5.4.1; Q&A No.41).
    The first call wins, so in a double knock-out whoever reached 0 first loses."""
    if state.winner == -1 and state.players[player_index].hp <= 0:
        state.winner = 1 - player_index


def check_damage_triggered_removal(state: GameState) -> None:
    """Remove damage-triggered area enchants immediately (Ground Rules 6.1.3.5;
    Q&A No.12, 16, 80). Skipped once the game is over (Q&A No.41)."""
    if state.winner != -1:
        return
    from .effects.removal import check_area_removal
    check_area_removal(state, damage_only=True)


def deal_damage(state: GameState, player_index: int, amount: int) -> None:
    """Effect damage to a player; counts toward PF_DAMAGE_TAKEN."""
    if amount <= 0:
        return
    player = state.players[player_index]
    old_hp = player.hp
    player.hp = max(0, player.hp - amount)
    player.flags[PF_DAMAGE_TAKEN] += amount
    if state.event_sink is not None:
        state.event_sink.append(
            (EVENT_HP_CHANGED, player_index, player.hp - old_hp, player.hp))
    record_hp_zero(state, player_index)
    check_damage_triggered_removal(state)


def resolve_battle(state: GameState) -> None:
    player_0, player_1 = state.players
    attack_0 = get_effective_attack(state, player_0)
    attack_1 = get_effective_attack(state, player_1)

    if attack_0 == attack_1:
        state.last_battle_winner = -1
        player_0.flags[PF_BATTLE_DAMAGE] = 0
        player_1.flags[PF_BATTLE_DAMAGE] = 0
        if state.event_sink is not None:
            state.event_sink.append((EVENT_BATTLE_RESULT, attack_0, attack_1, -1, 0))
        return

    winner, loser = (player_0, player_1) if attack_0 > attack_1 else (player_1, player_0)
    raw_damage = abs(attack_0 - attack_1)
    if winner.flags[PF_DAMAGE_NOT_REDUCIBLE]:
        reduction = 0
    else:
        reduction = loser.flags[PF_DAMAGE_REDUCTION]
    damage = max(0, raw_damage - reduction)

    loser.flags[PF_DAMAGE_REDUCED] = raw_damage - damage
    loser.hp = max(0, loser.hp - damage)
    loser.flags[PF_BATTLE_DAMAGE] = damage
    winner.flags[PF_BATTLE_DAMAGE] = 0
    loser.flags[PF_DAMAGE_TAKEN] += damage
    loser.flags[PF_BATTLE_LOST] = 1
    state.last_battle_winner = winner.index
    if state.event_sink is not None:
        state.event_sink.append(
            (EVENT_BATTLE_RESULT, attack_0, attack_1, winner.index, damage))
    record_hp_zero(state, loser.index)


def check_win(state: GameState) -> None:
    """HP win check; deck-out is handled by the end-turn phase. The both-at-zero
    branch only matters for states that set HP without going through record_hp_zero."""
    if state.winner != -1:
        return
    hp_0 = state.players[0].hp
    hp_1 = state.players[1].hp
    if hp_0 <= 0 and hp_1 <= 0:
        state.winner = 0 if hp_0 >= hp_1 else 1
    elif hp_0 <= 0:
        state.winner = 1
    elif hp_1 <= 0:
        state.winner = 0
