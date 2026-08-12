import test from 'node:test';
import assert from 'node:assert/strict';
import { calculateTeamWinProbability } from './predictionEngine.mjs';

function players(prefix, rc) {
  return [0, 1, 2, 3].map((index) => ({ id: `${prefix}${index}`, name: `${prefix}${index}`, rcRating: rc + index * 10, active: true }));
}

test('identical teams have a 50% match win probability', () => {
  const team = players('A', 1600);
  const probability = calculateTeamWinProbability(team, team.map((player) => ({ ...player, id: `B${player.id.slice(1)}` })));
  assert.ok(Math.abs(probability - 0.5) < 1e-10);
});

test('a materially stronger team has a higher match win probability', () => {
  const strong = players('A', 1800);
  const weak = players('B', 1400);
  const probability = calculateTeamWinProbability(strong, weak);
  assert.ok(probability > 0.95);
  assert.ok(probability <= 1);
});

test('match probability is always between zero and one', () => {
  const teamA = players('A', 1700);
  const teamB = players('B', 1500);
  const probability = calculateTeamWinProbability(teamA, teamB);
  assert.ok(probability >= 0 && probability <= 1);
});
