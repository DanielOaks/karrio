from purplship.server.serializers import owned_model_serializer, Serializer, Context, SerializerDecorator
from purplship.server.user.models import Token


@owned_model_serializer
class TokenSerializer(Serializer):

    def create(self, validated_data: dict, context: Context) -> Token:
        extra = dict(org__id=getattr(context.org, 'id', None)) if hasattr(Token, 'org') else {}
        token, *_ = Token.objects.get_or_create(user=context.user, **extra)
        return token

    @staticmethod
    def retrieve_token(context, org_id: str = None):
        user = getattr(context, 'user', None)
        if org_id is not None and hasattr(Token, 'org'):
            import purplship.server.orgs.models as orgs
            org = orgs.Organization.objects.get(id=org_id, users__id=getattr(user, 'id', None))
        else:
            org = getattr(context, 'org', None)

        ctx = Context(user, org)
        tokens = Token.access_by(ctx)

        return (
            tokens.first() if tokens.exists() else
            SerializerDecorator[TokenSerializer](data={}, context=ctx).save().instance
        )
