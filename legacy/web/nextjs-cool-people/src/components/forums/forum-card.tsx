import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import type { Forum } from '@/lib/types';
import Link from 'next/link';
import Image from 'next/image';
import { PlaceHolderImages } from '@/lib/placeholder-images';

export function ForumCard({ forum }: { forum: Forum }) {
  const forumImage = PlaceHolderImages.find(img => img.id === forum.image);

  return (
    <Link href={`/forums/${forum.slug}`} className="group block">
      <Card className="flex h-full flex-col overflow-hidden transition-all group-hover:border-primary group-hover:shadow-lg">
        {forumImage && (
          <div className="overflow-hidden">
            <Image
              src={forumImage.imageUrl}
              alt={forum.name}
              width={400}
              height={200}
              data-ai-hint={forumImage.imageHint}
              className="w-full object-cover transition-transform group-hover:scale-105"
            />
          </div>
        )}
        <CardHeader>
          <CardTitle className="font-headline">{forum.name}</CardTitle>
          <CardDescription>{forum.description}</CardDescription>
        </CardHeader>
        <CardFooter className="mt-auto flex text-xs text-muted-foreground">
          <span>{forum.threadCount.toLocaleString()} threads</span>
          <span className="mx-2">·</span>
          <span>{forum.postCount.toLocaleString()} posts</span>
        </CardFooter>
      </Card>
    </Link>
  );
}
