// Friendly names for the generated OpenAPI types (run `npm run gen:api` after API changes).
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Me = Schemas["MeResponse"];
export type Installation = Schemas["InstallationOut"];
export type InstallationsResponse = Schemas["InstallationsResponse"];
export type Repository = Schemas["RepositoryOut"];
export type RepositoryConfig = Schemas["RepositoryConfigOut"];
export type Pull = Schemas["PullOut"];
export type PullDetail = Schemas["PullDetailOut"];
export type PullPage = Schemas["Page_PullOut_"];
export type RunSummary = Schemas["RunSummaryOut"];
export type RunDetail = Schemas["RunDetailOut"];
export type Comment = Schemas["CommentOut"];
export type LLMCall = Schemas["LLMCallOut"];
export type Analytics = Schemas["AnalyticsOverview"];
export type Rate = Schemas["RateOut"];
export type Range = "7d" | "30d" | "90d";
export type Severity = "critical" | "high" | "medium" | "low" | "info";
