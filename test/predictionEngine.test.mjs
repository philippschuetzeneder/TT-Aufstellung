import assert from 'node:assert/strict';
import test from 'node:test';
import { historicalCombinations, players } from '../src/data/demoData.mjs';
import { buildOpponentCombinationPredictions } from '../src/engine/predictionEngine.mjs';

const opponents = players.filter((player) => player.clubId === 'ask-beispielverein');

test('probabilities sum to approximately 100 percent', () => {
  const predictions = buildOpponentCombinationPredictions(opponents, historicalCombinations);
  const total = predictions.reduce((sum, prediction) => sum + prediction.probability, 0);
  assert.ok(Math.abs(total - 1) < 1e-8);
});

test('a frequently used player group receives a higher probability', () => {
  const predictions = buildOpponentCombinationPredictions(opponents, historicalCombinations);
  const frequent = predictions.find((item) => item.playerIds.every((id) => ['opp-x', 'opp-y', 'opp-z', 'opp-w'].includes(id)));
  const rare = predictions.find((item) => item.playerIds.every((id) => ['opp-y', 'opp-z', 'opp-w', 'opp-v'].includes(id)));
  assert.ok(frequent.probability > rare.probability);
});

test('unavailable players are excluded', () => {
  const predictions = buildOpponentCombinationPredictions(opponents, historicalCombinations);
  assert.equal(predictions.flatMap((item) => item.playerIds).includes('opp-u'), false);
});

test('only valid four-player combinations are generated', () => {
  const predictions = buildOpponentCombinationPredictions(opponents, historicalCombinations);
  for (const prediction of predictions) {
    assert.equal(prediction.playerIds.length, 4);
    assert.equal(new Set(prediction.playerIds).size, 4);
  }
});

test('different RC ratings influence the calculation', () => {
  const highRc = opponents.map((player) => ({ ...player, active: player.id !== 'opp-u', availability: 0.8 }));
  const lowRc = highRc.map((player) => player.id === 'opp-x' ? { ...player, rcRating: 1200 } : player);
  const highRatedXProbability = buildOpponentCombinationPredictions(highRc, [])
    .filter((item) => item.playerIds.includes('opp-x'))
    .reduce((sum, item) => sum + item.probability, 0);
  const lowRatedXProbability = buildOpponentCombinationPredictions(lowRc, [])
    .filter((item) => item.playerIds.includes('opp-x'))
    .reduce((sum, item) => sum + item.probability, 0);
  assert.ok(highRatedXProbability > lowRatedXProbability);
});
