const RC_WEIGHT = 0.42;
const AVAILABILITY_WEIGHT = 0.38;
const HISTORY_WEIGHT = 0.2;
const RECENCY_HALF_LIFE_DAYS = 45;
const RC_SCALE = 400;

// Fixed Austrian 4-player match schedule, read from the XTTV match sheet.
// A-D are the four positions of one team. The opponent is the other orientation.
// Games 1-4 and 6-9 give every player exactly two singles; 11-14 give every
// player the third single. Games 5 and 10 are doubles and are modelled below.
const SINGLE_SCHEDULE = [
  ['B', 'D'], // 1
  ['C', 'A'], // 2
  ['D', 'C'], // 3
  ['A', 'B'], // 4
  ['B', 'A'], // 6
  ['C', 'D'], // 7
  ['A', 'C'], // 8
  ['D', 'B'], // 9
  ['A', 'A'], // 11
  ['B', 'B'], // 12
  ['C', 'C'], // 13
  ['D', 'D']  // 14
];

const POSITION_INDEX = { A: 0, B: 1, C: 2, D: 3 };

export function buildOpponentCombinationPredictions(opponentPlayers, historicalCombinations, options = {}) {
  const activePlayers = opponentPlayers.filter((player) => player.active);
  const combinations = choose(activePlayers, 4);
  if (combinations.length === 0) return [];
  const historicalLookup = new Map(historicalCombinations.map((item) => [canonicalKey(item.playerIds), item]));
  const maxRatingSum = Math.max(...combinations.map((group) => group.reduce((sum, player) => sum + player.rcRating, 0)));
  const maxHistorical = Math.max(1, ...historicalCombinations.map((item) => recencyAdjustedAppearances(item)));
  const scored = combinations.map((group) => {
    const playerIds = group.map((player) => player.id);
    const history = historicalLookup.get(canonicalKey(playerIds));
    const availability = geometricMean(group.map((player) => player.availability));
    const rcStrength = group.reduce((sum, player) => sum + player.rcRating, 0) / maxRatingSum;
    const historicalUsage = history ? recencyAdjustedAppearances(history) / maxHistorical : 0.08;
    const score = AVAILABILITY_WEIGHT * availability + RC_WEIGHT * rcStrength + HISTORY_WEIGHT * historicalUsage;
    return { playerIds, probability: 0, score, factors: { availability, rcStrength, historicalUsage } };
  });
  return normalize(scored).sort((a, b) => b.probability - a.probability).slice(0, options.maxCombinations ?? scored.length);
}

/** Uses hidden opponent scenarios to find the own four-player order with the highest expected team win probability. */
export function buildOptimalOwnLineup(ownPlayers, opponentPlayers, opponentPredictions) {
  if (ownPlayers.length !== 4 || !opponentPlayers?.length || !opponentPredictions?.length) return null;
  const opponentById = new Map(opponentPlayers.map((player) => [player.id, player]));
  const scenarios = opponentPredictions.map((prediction) => {
    const players = prediction.playerIds.map((id) => opponentById.get(id)).filter(Boolean);
    return players.length === 4 ? { probability: prediction.probability, variants: buildPositionVariants(players) } : null;
  }).filter(Boolean);
  if (!scenarios.length) return null;
  const scenarioTotal = scenarios.reduce((sum, scenario) => sum + scenario.probability, 0) || 1;

  const candidates = permute(ownPlayers).map((lineup) => {
    let teamWinProbability = 0;
    for (const scenario of scenarios) {
      const scenarioWeight = scenario.probability / scenarioTotal;
      for (const variant of scenario.variants) {
        const opponents = variant.playerIds.map((id) => opponentById.get(id));
        teamWinProbability += scenarioWeight * variant.probability * calculateTeamWinProbability(lineup, opponents);
      }
    }
    return { playerIds: lineup.map((player) => player.id), teamWinProbability };
  }).sort((a, b) => b.teamWinProbability - a.teamWinProbability);

  return { best: candidates[0], alternatives: candidates.slice(1, 3), scenariosUsed: scenarios.length };
}

/**
 * Calculates the probability of winning the actual 14-game match.
 *
 * Rules currently modelled:
 * - 12 fixed singles: every player plays exactly 3 singles.
 * - Games 1-10 are always played. Games 5 and 10 are doubles.
 * - Game 5: the two strongest players by RC form the double.
 * - Game 10: the two weakest players by RC form the double.
 * - After game 10, 8 wins are enough to decide the match.
 * - If neither team has 8 wins after game 10, games 11-14 are played.
 * - A final 7:7 is a draw, not a team win.
 * - The horizontal/vertical orientation is unknown, so both orientations are
 *   averaged 50/50.
 */
export function calculateTeamWinProbability(ownLineup, opponentLineup) {
  if (ownLineup.length !== 4 || opponentLineup.length !== 4) return 0;
  return (
    calculateOrientedMatchProbability(ownLineup, opponentLineup) +
    calculateOrientedMatchProbability(opponentLineup, ownLineup, true)
  ) / 2;
}

function calculateOrientedMatchProbability(ownLineup, opponentLineup, reversed = false) {
  const singleGames = SINGLE_SCHEDULE.slice(0, 8).map(([ownPosition, opponentPosition]) => {
    const own = ownLineup[POSITION_INDEX[reversed ? opponentPosition : ownPosition]];
    const opponent = opponentLineup[POSITION_INDEX[reversed ? ownPosition : opponentPosition]];
    return headToHeadWinProbability(own.rcRating, opponent.rcRating);
  });

  const ownDoubles = strengthBasedDoubles(ownLineup);
  const opponentDoubles = strengthBasedDoubles(opponentLineup);
  const ownDouble5 = averageRc(ownDoubles.strongest);
  const opponentDouble5 = averageRc(opponentDoubles.strongest);
  const ownDouble10 = averageRc(ownDoubles.weakest);
  const opponentDouble10 = averageRc(opponentDoubles.weakest);

  const firstTen = [...singleGames,
    headToHeadWinProbability(ownDouble5, opponentDouble5),
    headToHeadWinProbability(ownDouble10, opponentDouble10)
  ];

  // First ten games are mandatory. Only after game 10 can the 8-win rule stop the match.
  let states = new Map([[0, 1]]);
  for (const probability of firstTen) states = addGame(states, probability);

  let winProbability = 0;
  for (const [ownWins, stateProbability] of states) {
    const opponentWins = 10 - ownWins;
    if (ownWins >= 8) {
      winProbability += stateProbability;
      continue;
    }
    if (opponentWins >= 8) continue;

    // Games 11-14 are the four fixed third singles.
    for (let i = 8; i < SINGLE_SCHEDULE.length; i += 1) {
      const [ownPosition, opponentPosition] = SINGLE_SCHEDULE[i];
      const own = ownLineup[POSITION_INDEX[reversed ? opponentPosition : ownPosition]];
      const opponent = opponentLineup[POSITION_INDEX[reversed ? ownPosition : opponentPosition]];
      // For the remaining four singles, calculate their result distribution.
      // This is independent of the score after game 10, so use a local DP below.
    }
    winProbability += stateProbability * probabilityOfWinningRemainingFour(
      ownLineup,
      opponentLineup,
      reversed,
      ownWins
    );
  }
  return winProbability;
}

function probabilityOfWinningRemainingFour(ownLineup, opponentLineup, reversed, startingWins) {
  let states = new Map([[startingWins, 1]]);
  for (let i = 8; i < SINGLE_SCHEDULE.length; i += 1) {
    const [ownPosition, opponentPosition] = SINGLE_SCHEDULE[i];
    const own = ownLineup[POSITION_INDEX[reversed ? opponentPosition : ownPosition]];
    const opponent = opponentLineup[POSITION_INDEX[reversed ? ownPosition : opponentPosition]];
    states = addGame(states, headToHeadWinProbability(own.rcRating, opponent.rcRating));
  }
  let probability = 0;
  for (const [wins, stateProbability] of states) {
    const losses = 14 - wins;
    if (wins > losses) probability += stateProbability;
  }
  return probability;
}

function addGame(states, winProbability) {
  const next = new Map();
  for (const [wins, probability] of states) {
    next.set(wins, (next.get(wins) || 0) + probability * (1 - winProbability));
    next.set(wins + 1, (next.get(wins + 1) || 0) + probability * winProbability);
  }
  return next;
}

function strengthBasedDoubles(lineup) {
  const sorted = [...lineup].sort((a, b) => b.rcRating - a.rcRating);
  return { strongest: [sorted[0], sorted[1]], weakest: [sorted[2], sorted[3]] };
}
function averageRc(players) { return players.reduce((sum, player) => sum + player.rcRating, 0) / players.length; }

export function buildPositionVariants(players) {
  return normalizeBy(permute(players).map((lineup) => {
    const expectedStrength = lineup.reduce((sum, player, index) => sum + player.rcRating * (4 - index), 0) / 10_000;
    return { playerIds: lineup.map((player) => player.id), probability: 0, expectedStrength };
  }), (variant) => Math.exp(variant.expectedStrength / 3)).sort((a, b) => b.probability - a.probability);
}

function headToHeadWinProbability(ownRc, opponentRc) { return 1 / (1 + Math.pow(10, (opponentRc - ownRc) / RC_SCALE)); }
function normalize(items) {
  const total = items.reduce((sum, item) => sum + item.score, 0);
  return items.map((item) => ({ ...item, probability: total === 0 ? 0 : item.score / total }));
}
function normalizeBy(items, scorer) {
  const scores = items.map(scorer);
  const total = scores.reduce((sum, score) => sum + score, 0);
  return items.map((item, index) => ({ ...item, probability: total === 0 ? 0 : scores[index] / total }));
}
function choose(items, size) {
  if (size === 0) return [[]];
  if (items.length < size) return [];
  const [head, ...tail] = items;
  return [...choose(tail, size - 1).map((combo) => [head, ...combo]), ...choose(tail, size)];
}
function permute(items) {
  if (items.length <= 1) return [items];
  return items.flatMap((item, index) => permute([...items.slice(0, index), ...items.slice(index + 1)]).map((rest) => [item, ...rest]));
}
function geometricMean(values) { return Math.pow(values.reduce((product, value) => product * Math.max(value, 0.01), 1), 1 / values.length); }
function recencyAdjustedAppearances(combination) { return combination.appearances * Math.pow(0.5, combination.lastUsedMatchDaysAgo / RECENCY_HALF_LIFE_DAYS); }
function canonicalKey(ids) { return [...ids].sort().join('|'); }
