import { historicalCombinations, leagues, matches, players, teams } from './demoData.mjs';

export class DemoDataProvider {
  async getLeagues() { return leagues; }
  async getTeams() { return teams; }
  async getPlayers() { return players; }
  async getMatches() { return matches; }
  async getHistoricalCombinations(teamId) { return historicalCombinations.filter((item) => item.teamId === teamId); }
}
