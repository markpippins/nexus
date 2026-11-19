import Image from 'next/image';
import { PlaceHolderImages } from '@/lib/placeholder-images';

type HeroBannerProps = {
  title: string;
  subtitle: string;
  imageId: string;
};

export function HeroBanner({ title, subtitle, imageId }: HeroBannerProps) {
  const image = PlaceHolderImages.find(img => img.id === imageId);

  return (
    <div className="relative h-64 w-full overflow-hidden">
      {image && (
        <Image
          src={image.imageUrl}
          alt={image.description}
          fill
          className="object-cover"
          data-ai-hint={image.imageHint}
        />
      )}
      <div className="absolute inset-0 bg-black/50" />
      <div className="relative z-10 flex h-full flex-col items-center justify-center text-center text-white">
        <h1 className="text-4xl font-bold font-headline tracking-tight md:text-5xl">
          {title}
        </h1>
        <p className="mt-2 max-w-2xl text-lg text-white/90">{subtitle}</p>
      </div>
    </div>
  );
}
