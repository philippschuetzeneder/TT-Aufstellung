const RC_WEIGHT = 0.42;
const AVAILABILITY_WEIGHT = 0.38;
const HISTORY_WEIGHT = 0.2;
const RECENCY_HALF_LIFE_DAYS = 45;
const RC_SCALE = 400;

// Austrian 4-player team-match demo schedule: 12 singles (3 per player) + 2 doubles.
// The exact historical schedule will be supplied by XTTV once imported.
const DEMO_SINGLE_PAIRINGS = [
  [0, 0], [1, 1], [2, 2], [3, 3],
  [0, 1], [1, 2], [2, 3], [3, 0],
  [0, 2], [1, 3], [2, 0], [3, 1]
];
const DEMO_DOUBLES = [
  [[0, 1], [0, 1]],
  [[2, 3], [2, 3]]
];

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
 * Estimates the probability that the team wins the complete 14-game match:
 * 12 singles (3 per player) plus 2 doubles. This is deliberately a transparent
 * demo model; XTTV will later provide the exact league-specific match schedule.
 */
export function calculateTeamWinProbability(ownLineup, opponentLineup) {
  if (ownLineup.length !== 4 || opponentLineup.length !== 4) return 0;
  const gameProbabilities = DEMO_SINGLE_PAIRINGS.map(([ownIndex, opponentIndex]) =>
    headToHeadWinProbability(ownLineup[ownIndex].rcRating, opponentLineup[opponentIndex].rcRating)
  );
  for (const [[ownA, ownB], [oppA, oppB]] of DEMO_DOUBLES) {
    const ownDoubleRc = (ownLineup[ownA].rcRating + ownLineup[ownB].rcRating) / 2;
    const oppDoubleRc = (opponentLineup[oppA].rcRating + opponentLineup[oppB].rcRating) / 2;
    gameProbabilities.push(headToHeadWinProbability(ownDoubleRc, oppDoubleRc));
  }

  // 14 scheduled games; a team wins the match with at least 8 wins.
  let distribution = [1];
  for (const probability of gameProbabilities) {
    const next = Array(distribution.length + 1).fill(0);
    distribution.forEach((value, wins) => {
      next[wins] += value * (1 - probability);
      next[wins + 1] += value * probability;
    });
    distribution = next;
  }
  return distribution.slice(8).reduce((sum, probability) => sum + probability, 0);
}

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
