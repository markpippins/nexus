'use client';

import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Bot, Loader2, Send, X } from 'lucide-react';
import {
  useEffect,
  useRef,
  useState,
  useTransition,
  useActionState,
} from 'react';
import { useFormStatus } from 'react-dom';
import { createPostAction, suggestTagsAction } from '@/lib/actions';
import { useToast } from '@/hooks/use-toast';

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <Button size="sm" disabled={pending}>
      {pending ? (
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
      ) : (
        <Send className="mr-2 h-4 w-4" />
      )}
      Post
    </Button>
  );
}

export function CreatePost() {
  const [content, setContent] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [suggestedTags, setSuggestedTags] = useState<string[]>([]);
  const [isSuggesting, startSuggestingTransition] = useTransition();
  const formRef = useRef<HTMLFormElement>(null);
  const { toast } = useToast();

  const [formState, formAction] = useActionState(createPostAction, {
    message: '',
    type: '',
  });

  useEffect(() => {
    if (formState?.type === 'success') {
      toast({
        title: 'Success!',
        description: formState.message,
      });
      formRef.current?.reset();
      setContent('');
      setTags([]);
      setSuggestedTags([]);
    } else if (formState?.type === 'error') {
      toast({
        title: 'Error',
        description: formState.message,
        variant: 'destructive',
      });
    }
  }, [formState, toast]);

  const handleSuggestTags = () => {
    if (content.length < 10) {
      toast({
        title: 'Content too short',
        description:
          'Please write at least 10 characters to get tag suggestions.',
        variant: 'destructive',
      });
      return;
    }
    startSuggestingTransition(async () => {
      const result = await suggestTagsAction(content);
      if (result.error) {
        toast({
          title: 'AI Error',
          description: result.error,
          variant: 'destructive',
        });
      } else {
        setSuggestedTags(
          result.suggestedTags.filter(st => !tags.includes(st))
        );
      }
    });
  };

  const addTag = (tag: string) => {
    if (!tags.includes(tag)) {
      setTags([...tags, tag]);
    }
    setSuggestedTags(suggestedTags.filter(st => st !== tag));
  };

  const removeTag = (tagToRemove: string) => {
    setTags(tags.filter(tag => tag !== tagToRemove));
  };

  return (
    <Card>
      <form action={formAction} ref={formRef}>
        <CardHeader className="p-4">
          <div className="flex items-start gap-4">
            <Avatar>
              <AvatarImage src="https://picsum.photos/seed/currentuser/100/100" />
              <AvatarFallback>U</AvatarFallback>
            </Avatar>
            <div className="w-full">
              <Textarea
                name="content"
                placeholder="What's on your mind?"
                className="w-full border-none focus-visible:ring-0"
                value={content}
                onChange={e => setContent(e.target.value)}
                rows={3}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 p-4 pt-0">
          <div className="flex flex-wrap gap-2">
            {tags.map(tag => (
              <Badge key={tag} variant="secondary">
                {tag}
                <input type="hidden" name="tags" value={tag} />
                <button
                  type="button"
                  onClick={() => removeTag(tag)}
                  className="ml-1 rounded-full p-0.5 hover:bg-destructive/20"
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
          </div>
          {suggestedTags.length > 0 && (
            <div className="space-y-2 rounded-md border border-dashed p-2">
              <p className="text-xs font-medium text-muted-foreground">
                AI Suggestions:
              </p>
              <div className="flex flex-wrap gap-2">
                {suggestedTags.map(tag => (
                  <Button
                    key={tag}
                    size="sm"
                    variant="outline"
                    onClick={() => addTag(tag)}
                  >
                    + {tag}
                  </Button>
                ))}
              </div>
            </div>
          )}
        </CardContent>
        <CardFooter className="flex justify-between p-4 pt-0">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleSuggestTags}
            disabled={isSuggesting}
          >
            {isSuggesting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Bot className="mr-2 h-4 w-4" />
            )}
            Suggest Tags
          </Button>
          <SubmitButton />
        </CardFooter>
      </form>
    </Card>
  );
}
