"""Attack computation, battle resolution, win checks, and the engine-inline
passive effects that modify them (02-005, 02-007, 03-061, 03-026, 01-005 flag,
04-099 override).

get_effective_attack precedence (matching the old engine exactly):
  04-099 attack override  >  power-cost gate (attack = 0)  >
  02-007 force-day  >  01-005 day/night reversal  >
  day/night base + attack bonus (floored at 0).
"""

from __future__ import annotations

from .cards import (
    ATK_DAY_T, ATK_NIGHT_T, POWER_COST_T, SEND_TO_POWER_T, EFFECT_T,
    EFFECT_TO_INDEX,
)
from .state import (
    GameState, PlayerState,
    PF_ATTACK_BONUS, PF_DAMAGE_REDUCTION, PF_DAY_NIGHT_REVERSED, PF_POWER_BONUS,
    PF_ATTACK_OVERRIDE, PF_DAMAGE_NOT_REDUCIBLE, PF_BATTLE_DAMAGE,
    PF_DAMAGE_TAKEN, PF_BATTLE_LOST, PF_DAMAGE_REDUCED,
    GF_MIDNIGHT_EXTENDED, GF_DAY_TO_NIGHT, GF_NIGHT_TO_DAY,
)

CHRONOS_SIZE = 18
MIDNIGHT = 4
NIGHT_END = 8
NOON = 13

# Engine-inline passive effect ids (dense effect indices).
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
    """True when `player`'s set-zone-C area enchant is `effect_index` and its
    power cost is met (area enchants use total_power only, no power bonus)."""
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


def set_chronos(state: GameState, new_value: int) -> None:
    """Set chronos directly, tracking any day/night transition (old set_chronos)."""
    old_is_night = state.chronos <= NIGHT_END
    new_is_night = new_value <= NIGHT_END
    if old_is_night and not new_is_night:
        state.gflags[GF_NIGHT_TO_DAY] = 1
    elif not old_is_night and new_is_night:
        state.gflags[GF_DAY_TO_NIGHT] = 1
    state.chronos = new_value


def advance_chronos_by(state: GameState, steps: int) -> None:
    """Advance chronos by `steps`, tracking transitions step-by-step."""
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


def get_effective_attack(state: GameState, player: PlayerState) -> int:
    if player.battle == -1:
        return 0

    override = player.flags[PF_ATTACK_OVERRIDE]
    if override != -1:
        return override

    battle_instance = player.battle
    def_index = state.inst_def[battle_instance]
    cost = effective_power_cost(state, battle_instance)
    power = total_power(state, player) + player.flags[PF_POWER_BONUS]
    if power < cost:
        return 0

    if force_day_active(state, player.index):
        base = ATK_DAY_T[def_index]
    elif player.flags[PF_DAY_NIGHT_REVERSED]:
        base = ATK_DAY_T[def_index] if state.is_night else ATK_NIGHT_T[def_index]
    else:
        base = ATK_NIGHT_T[def_index] if state.is_night else ATK_DAY_T[def_index]

    attack = base + player.flags[PF_ATTACK_BONUS]
    return attack if attack > 0 else 0


def get_effective_attack_ignoring_override(state: GameState, player: PlayerState) -> int:
    """Attack computation as inlined by 04-084/04-101: identical to
    get_effective_attack except the 04-099 attack override is NOT consulted."""
    if player.battle == -1:
        return 0
    battle_instance = player.battle
    def_index = state.inst_def[battle_instance]
    cost = effective_power_cost(state, battle_instance)
    power = total_power(state, player) + player.flags[PF_POWER_BONUS]
    if power < cost:
        return 0
    if force_day_active(state, player.index):
        base = ATK_DAY_T[def_index]
    elif player.flags[PF_DAY_NIGHT_REVERSED]:
        base = ATK_DAY_T[def_index] if state.is_night else ATK_NIGHT_T[def_index]
    else:
        base = ATK_NIGHT_T[def_index] if state.is_night else ATK_DAY_T[def_index]
    attack = base + player.flags[PF_ATTACK_BONUS]
    return attack if attack > 0 else 0


def deal_damage(state: GameState, player_index: int, amount: int) -> None:
    """Effect damage to a player's HP; counts toward damage_taken_this_turn."""
    if amount <= 0:
        return
    player = state.players[player_index]
    player.hp = max(0, player.hp - amount)
    player.flags[PF_DAMAGE_TAKEN] += amount


def resolve_battle(state: GameState) -> None:
    player_0, player_1 = state.players
    attack_0 = get_effective_attack(state, player_0)
    attack_1 = get_effective_attack(state, player_1)

    if attack_0 == attack_1:
        state.last_battle_winner = -1
        player_0.flags[PF_BATTLE_DAMAGE] = 0
        player_1.flags[PF_BATTLE_DAMAGE] = 0
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


def check_win(state: GameState) -> None:
    """HP-based win check (old check_win_condition). Deck-out is handled in
    end_turn. Both at HP <= 0: higher HP wins; exact tie goes to player 0."""
    hp_0 = state.players[0].hp
    hp_1 = state.players[1].hp
    if hp_0 <= 0 and hp_1 <= 0:
        state.winner = 0 if hp_0 >= hp_1 else 1
    elif hp_0 <= 0:
        state.winner = 1
    elif hp_1 <= 0:
        state.winner = 0
