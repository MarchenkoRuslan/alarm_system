import { apiRequest } from "./client";
import type { Rule } from "../types/rule";

type RulesResponse = {
  rules: Rule[];
};

export async function getRules() {
  const data = await apiRequest<RulesResponse>("/internal/rules");

  return data.rules;
}
