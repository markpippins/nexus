
import { HeroBanner } from '@/components/hero-banner';
import { SignupCard } from '@/components/signup-card';

export default function LandingPage() {
  return (
    <div>
      <HeroBanner
        title="Welcome to CoolPeople"
        subtitle="The place to connect and share with like-minded individuals."
        imageId="homepage_banner"
      />
      <div className="container mx-auto max-w-6xl px-4 py-8">
        <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
          <aside className="md:col-span-1">
            <div className="sticky top-20">
              <SignupCard />
            </div>
          </aside>
          <div className="space-y-8 md:col-span-2">
            <div className="rounded-lg border bg-card p-6 text-center">
              <h2 className="text-xl font-bold font-headline">
                Join the conversation!
              </h2>
              <p className="mt-2 text-muted-foreground">
                Log in or sign up to see the latest posts from the community.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
