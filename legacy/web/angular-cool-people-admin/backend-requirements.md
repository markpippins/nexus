# Backend Requirements for Angular Admin Dashboard

The following operations are required to support a comprehensive Admin Dashboard. Currently, the backend lacks pagination, advanced filtering, and some specific management operations.

## General Requirements
- **Pagination**: All `findAll` operations (Users, Profiles, Forums, Posts) must support pagination (`page`, `size`, `sort`). Returning full sets is not scalable for an admin view.
- **Search/Filtering**: Operations to search entities by partial string matches (e.g., `searchUsers(query)`, `searchPosts(text)`).

## Service-Specific Gaps

### ProfileService
- **[MISSING] `findAll`**: No operation to list all profiles. Required for the "Profiles" data table.
- **[MISSING] `updateProfile`**: Explicit update operation that handles partial updates (patch) rather than full overwrite.

### ForumService
- **[MISSING] `addMember` / `removeMember`**: Operations to manage forum membership directly.
- **[MISSING] `getMembers`**: Paginated retrieval of forum members.

### PostService
- **[MISSING] `findByUser`**: Retrieve all posts by a specific `userId`.
- **[MISSING] `deleteComment`**: Explicit operation to delete a comment (if not covered by generic delete).

### CommentService
- **[MISSING] `findAllPaginated`**: Pagination for comments list.
- **[MISSING] `findByUser`**: Retrieve all comments by a specific user.

## Proposed New Broker Operations

```java
// UserService
@BrokerOperation("findAllPaginated")
public Page<UserDTO> findAll(@BrokerParam("page") int page, @BrokerParam("size") int size);

@BrokerOperation("search")
public List<UserDTO> search(@BrokerParam("query") String query);

// ProfileService
@BrokerOperation("findAllPaginated")
public Page<ProfileDTO> findAll(@BrokerParam("page") int page, @BrokerParam("size") int size);

// PostService
@BrokerOperation("findByUserPaginated")
public Page<PostDTO> findByUser(@BrokerParam("userId") String userId, @BrokerParam("page") int page, @BrokerParam("size") int size);
```

## Next.js Application Requirements
The following fields are used in the Next.js frontend but appear to be missing or not standard in the current backend DTOs:

### PostDTO
- **[MISSING] `tags`**: List of string tags associated with a post.
- **[MISSING] `replies`**: Count of replies/comments (or the list itself).

### ForumDTO
- **[MISSING] `threadCount`**: Total number of threads/posts in the forum.
- **[MISSING] `postCount`**: Total number of replies in the forum.

### UserDTO
- **[MISSING] `avatarUrl`**: URL to the user's avatar image.
