/** Domain model shapes are documented with JSDoc so the vanilla MVP stays dependency-free. */
export const MODEL_FIELDS = Object.freeze({
  player: ['id', 'name', 'clubId', 'rcRating', 'active'],
  team: ['id', 'name', 'leagueId', 'players'],
  match: ['id', 'season', 'leagueId', 'homeTeam', 'awayTeam', 'date'],
  prediction: ['playerIds', 'probability']
});
