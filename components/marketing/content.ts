// Copy that appears in more than one place. Everything here describes what the product does today (see README.md).

export const SAMPLE_CALL = [
  { who: "Receptionist", say: "Thank you for calling Maple Street Dental. I’m an automated assistant. How can I help you today?" },
  { who: "Caller", say: "Can I get a cleaning next Tuesday?" },
  { who: "Receptionist", say: "Tuesday, I have 9:00 AM, 11:30 AM, or 2:30 PM. Which works best for you?" },
  { who: "Caller", say: "2:30, please." },
  { who: "Receptionist", say: "You’re all set! Your cleaning is booked for Tuesday at 2:30 PM. Is there anything else I can help with?" },
] as const;

export const FAQ = [
  {
    q: "Will callers know they’re talking to AI?",
    a: "Yes, by default. The receptionist says it’s an automated assistant at the start of the call. You can change this under Data & privacy, but AI-disclosure and call-recording rules differ by state, and following them is your responsibility.",
  },
  {
    q: "Do I need a new phone number?",
    a: "No. You can forward the number you already have, or connect a line through a SIP provider. You pay that provider for the number and the minutes. Forwarding options depend on your carrier, and porting a number takes days.",
  },
  {
    q: "What does it cost?",
    a: "We’re in early access and quote each business individually, so there’s no public price list yet. There are no per-call AI fees. Your phone provider bills you separately for the number and minutes. Send us a message and we’ll give you a quote.",
  },
  {
    q: "Can I try it before connecting my phone?",
    a: "Yes. Create an account, add your hours and services, and run a test call in your browser using your microphone or by typing. Nothing has to be connected to a phone line for that.",
  },
  {
    q: "What happens if it doesn’t understand someone?",
    a: "It asks again and reads names, numbers, dates and times back before acting. After repeated misunderstandings it takes a message so your team can call back. On a phone line with a transfer number set, it can also pass the caller to your team during open hours.",
  },
  {
    q: "What does it do in an emergency?",
    a: "When a caller describes an emergency, it says the message you wrote before anything else and sends your team an urgent alert. It isn’t an emergency service, and it never gives medical, dental or legal advice.",
  },
  {
    q: "Which languages does it speak?",
    a: "English only for now. Unusual phrasing can still trip it up, which is why it offers a human callback instead of guessing.",
  },
  {
    q: "Can it send text message reminders to my customers?",
    a: "Not today. SMS isn’t built. Reminders go to your team, in the app, by push notification or by e-mail, at the times each person chooses.",
  },
  {
    q: "Does it record calls?",
    a: "Not unless you turn it on. Recording is off by default and needs a consent message that callers hear first. Transcripts are kept for 90 days and recordings for 30 days unless you change those settings.",
  },
  {
    q: "Where does the speech processing happen?",
    a: "Speech is turned into text and the receptionist’s replies are turned into speech by trusted online speech services, and an AI service is asked only about sentences our own rules couldn’t understand. Your privacy policy page lists what is sent and to whom. Your phone provider also carries the call itself.",
  },
  {
    q: "Is it HIPAA compliant?",
    a: "We don’t claim HIPAA compliance. If your practice handles protected health information, talk to us before you connect a live phone line so we can work out whether it’s a fit.",
  },
  {
    q: "Can it book for more than one person on my team?",
    a: "Yes. Add each staff member with the services they offer and the hours they work, and the receptionist only offers times when that person is free. Every business’s data is kept separate from every other business’s.",
  },
] as const;

export const LEDGER = [
  { item: "Your OrbiTech plan", payee: "OrbiTech Solution", note: "Quoted during early access, before you’re charged anything." },
  { item: "AI speech and conversation", payee: "Nobody", note: "No per-call AI fees. The speech models are open source and run on our server." },
  { item: "Phone number and minutes", payee: "Your phone provider", note: "Billed by the provider you choose. Rates vary." },
  { item: "Forwarding your existing number", payee: "Your carrier", note: "Options and costs depend on your carrier." },
] as const;
