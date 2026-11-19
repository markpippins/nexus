export type User = {
  id: string;
  alias: string;
  email: string;
  identifier: string;
  avatarUrl: string;
  followers?: string[];
  following?: string[];
  friends?: string[];
};

export type UserFormValues = {
  id?: string;
  alias: string;
  email: string;
  identifier: string;
};