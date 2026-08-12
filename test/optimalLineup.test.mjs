import assert from 'node:assert/strict';
import test from 'node:test';
import { players } from '../src/data/demoData.mjs';
import { buildOpponentCombinationPredictions, buildOptimalOwnLineup } from '../src/engine/predictionEngine.mjs';

const ownPlayers = players.filter((player) => player.clubId === 'union-beispielstadt' && player.active).slice(0, 4);
const opponents = players.filter((player) => player.clubId === 'ask-beispielverein');

test('calculates an optimal own lineup from hidden opponent scenarios', () => {
  const predictions = buildOpponentCombinationPredictions(opponents, []);
  const result = buildOptimalOwnLineup(ownPlayers, opponents, predictions);
  assert.ok(result?.best);
  assert.equal(result.best.playerIds.length, 4);
  assert.equal(new Set(result.best.playerIds).size, 4);
  assert.ok(result.best.teamWinProbability >= 0 && result.best.teamWinProbability <= 1);
});
