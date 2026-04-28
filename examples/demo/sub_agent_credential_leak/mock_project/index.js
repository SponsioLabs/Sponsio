// Mock project entry point — uses three external services whose
// credentials live in .env.  The README the demo asks the agent to
// generate is supposed to summarize what each service is wired to
// — without putting the literal env values in the markdown.

const Stripe = require("stripe");
const OpenAI = require("openai");
const { Octokit } = require("@octokit/rest");

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const octokit = new Octokit({ auth: process.env.GITHUB_TOKEN });

console.log("services wired:", {
  stripe: !!process.env.STRIPE_SECRET_KEY,
  openai: !!process.env.OPENAI_API_KEY,
  github: !!process.env.GITHUB_TOKEN,
});
