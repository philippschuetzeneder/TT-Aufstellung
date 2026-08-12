export const leagues = [{ id: 'liga-ooe-1', name: 'Demo Oberösterreich Liga A' }];
export const players = [
  { id: 'own-a', name: 'Spieler A', clubId: 'union-beispielstadt', rcRating: 1680, active: true, availability: 0.98 },
  { id: 'own-b', name: 'Spieler B', clubId: 'union-beispielstadt', rcRating: 1540, active: true, availability: 0.95 },
  { id: 'own-c', name: 'Spieler C', clubId: 'union-beispielstadt', rcRating: 1490, active: true, availability: 0.92 },
  { id: 'own-d', name: 'Spieler D', clubId: 'union-beispielstadt', rcRating: 1420, active: true, availability: 0.9 },
  { id: 'own-e', name: 'Spieler E', clubId: 'union-beispielstadt', rcRating: 1360, active: true, availability: 0.5 },
  { id: 'opp-x', name: 'Spieler X', clubId: 'ask-beispielverein', rcRating: 1710, active: true, availability: 0.91 },
  { id: 'opp-y', name: 'Spieler Y', clubId: 'ask-beispielverein', rcRating: 1630, active: true, availability: 0.88 },
  { id: 'opp-z', name: 'Spieler Z', clubId: 'ask-beispielverein', rcRating: 1510, active: true, availability: 0.84 },
  { id: 'opp-w', name: 'Spieler W', clubId: 'ask-beispielverein', rcRating: 1460, active: true, availability: 0.78 },
  { id: 'opp-v', name: 'Spieler V', clubId: 'ask-beispielverein', rcRating: 1390, active: true, availability: 0.66 },
  { id: 'opp-u', name: 'Spieler U', clubId: 'ask-beispielverein', rcRating: 1330, active: false, availability: 0.2 }
];
export const teams = [
  { id: 'union-beispielstadt', name: 'Union Beispielstadt 1', leagueId: 'liga-ooe-1', players: ['own-a', 'own-b', 'own-c', 'own-d', 'own-e'] },
  { id: 'ask-beispielverein', name: 'ASK Beispielverein 1', leagueId: 'liga-ooe-1', players: ['opp-x', 'opp-y', 'opp-z', 'opp-w', 'opp-v', 'opp-u'] }
];
export const matches = [{ id: 'demo-match-1', season: '2026/27', leagueId: 'liga-ooe-1', homeTeam: 'union-beispielstadt', awayTeam: 'ask-beispielverein', date: '2026-09-19' }];
export const historicalCombinations = [
  { teamId: 'ask-beispielverein', playerIds: ['opp-x', 'opp-y', 'opp-z', 'opp-w'], appearances: 11, lastUsedMatchDaysAgo: 12 },
  { teamId: 'ask-beispielverein', playerIds: ['opp-x', 'opp-y', 'opp-z', 'opp-v'], appearances: 5, lastUsedMatchDaysAgo: 28 },
  { teamId: 'ask-beispielverein', playerIds: ['opp-x', 'opp-y', 'opp-w', 'opp-v'], appearances: 3, lastUsedMatchDaysAgo: 35 },
  { teamId: 'ask-beispielverein', playerIds: ['opp-x', 'opp-z', 'opp-w', 'opp-v'], appearances: 2, lastUsedMatchDaysAgo: 42 },
  { teamId: 'ask-beispielverein', playerIds: ['opp-y', 'opp-z', 'opp-w', 'opp-v'], appearances: 1, lastUsedMatchDaysAgo: 70 },
  { teamId: 'ask-beispielverein', playerIds: ['opp-x', 'opp-y', 'opp-z', 'opp-u'], appearances: 2, lastUsedMatchDaysAgo: 95 }
];
