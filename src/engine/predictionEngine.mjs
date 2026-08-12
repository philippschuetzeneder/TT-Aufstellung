const RC_WEIGHT = 0.42;
const AVAILABILITY_WEIGHT = 0.38;
const HISTORY_WEIGHT = 0.2;
const RECENCY_HALF_LIFE_DAYS = 45;

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

export function buildPositionVariants(players) {
  return normalizeBy(permute(players).map((lineup) => {
    const expectedStrength = lineup.reduce((sum, player, index) => sum + player.rcRating * (4 - index), 0) / 10_000;
    return { playerIds: lineup.map((player) => player.id), probability: 0, expectedStrength };
  }), (variant) => Math.exp(variant.expectedStrength / 3)).sort((a, b) => b.probability - a.probability);
}

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
